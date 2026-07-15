"""Model assembly: DINOv2-S backbone + projection head, freeze plan, param groups.

The backbone comes from torch.hub (``facebookresearch/dinov2``), matching the
model definition in ``scripts/export_embedder.py`` exactly, so the fine-tuned
export is a drop-in for the app's ``OnnxEmbedder``. xformers must NOT be
installed on the box: the hub code falls back to plain attention, which is both
what we train and the export-friendly path.

The projection head exists only to absorb the contrastive loss's aggressive
invariance compression (SimCLR-family finding); retrieval, eval, and the ONNX
export all use the raw backbone CLS features.
"""

from __future__ import annotations

import logging

import torch
from torch import nn

from training.config import IMAGENET_MEAN, IMAGENET_STD, INPUT_SIZE, TrainConfig

logger = logging.getLogger(__name__)

HUB_REPO = "facebookresearch/dinov2"
HUB_MODEL = "dinov2_vits14"
FEATURE_DIM = 384


class ProjectionHead(nn.Module):
    """MLP head mapping backbone features into the contrastive space."""

    def __init__(self, in_dim: int = FEATURE_DIM, hidden: int = 1024, out_dim: int = 256) -> None:
        """Build the head.

        Args:
            in_dim: Backbone feature dimension.
            hidden: Hidden layer width.
            out_dim: Contrastive space dimension.
        """
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.GELU(),
            nn.LayerNorm(hidden),
            nn.Linear(hidden, out_dim, bias=False),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        """Project and L2-normalize features."""
        return nn.functional.normalize(self.net(features), dim=-1)


class FineTuneModel(nn.Module):
    """DINOv2-S backbone with a projection head and a partial-freeze plan."""

    def __init__(self, freeze_blocks: int) -> None:
        """Load the backbone from torch.hub and apply the freeze plan.

        Args:
            freeze_blocks: Number of leading transformer blocks (plus the patch
                embed and position/cls tokens) to freeze.
        """
        super().__init__()
        self.backbone = torch.hub.load(HUB_REPO, HUB_MODEL)
        self.head = ProjectionHead()
        self._freeze(freeze_blocks)

    def _freeze(self, freeze_blocks: int) -> None:
        """Freeze the patch embed, tokens, and the first ``freeze_blocks`` blocks."""
        frozen = 0
        for name, param in self.backbone.named_parameters():
            freeze = name.startswith(("patch_embed", "cls_token", "pos_embed", "mask_token"))
            for i in range(freeze_blocks):
                if name.startswith(f"blocks.{i}."):
                    freeze = True
            if freeze:
                param.requires_grad_(False)
                frozen += param.numel()
        logger.info("froze %.1fM backbone parameters", frozen / 1e6)

    def features(self, x: torch.Tensor) -> torch.Tensor:
        """Return raw backbone CLS features (the deployed representation)."""
        out: torch.Tensor = self.backbone(x)
        return out

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Return projected, L2-normalized contrastive features."""
        return self.head(self.features(x))


def param_groups(model: FineTuneModel, cfg: TrainConfig) -> list[dict]:
    """Build AdamW parameter groups with layer-wise LR decay.

    The head trains at ``head_lr``. Backbone blocks train at ``backbone_lr``
    scaled by ``layer_decay`` per block downward from the top; norms and biases
    get no weight decay.

    Args:
        model: The assembled model (freeze plan already applied).
        cfg: Training configuration.

    Returns:
        Parameter-group dicts for AdamW.
    """
    num_blocks = len(model.backbone.blocks)
    groups: dict[tuple[float, float], list[torch.nn.Parameter]] = {}

    def _add(param: torch.nn.Parameter, lr: float, wd: float) -> None:
        groups.setdefault((lr, wd), []).append(param)

    for name, param in model.backbone.named_parameters():
        if not param.requires_grad:
            continue
        block = num_blocks - 1  # final norm etc. counts as the top layer
        if name.startswith("blocks."):
            block = int(name.split(".")[1])
        lr = cfg.backbone_lr * (cfg.layer_decay ** (num_blocks - 1 - block))
        wd = 0.0 if param.ndim <= 1 else cfg.weight_decay
        _add(param, lr, wd)
    for param in model.head.parameters():
        wd = 0.0 if param.ndim <= 1 else cfg.weight_decay
        _add(param, cfg.head_lr, wd)

    return [{"params": params, "lr": lr, "weight_decay": wd} for (lr, wd), params in groups.items()]


def preprocess(batch01: torch.Tensor) -> torch.Tensor:
    """Squash-resize a [0,1] batch to the encoder input and ImageNet-normalize.

    Matches the app's ``OnnxEmbedder`` preprocessing semantics: a
    non-aspect-preserving resize to ``INPUT_SIZE`` square, then mean/std
    normalization. (Training uses GPU bilinear where the app uses PIL bicubic —
    a bounded skew the parity check and blur augmentation both cover.)

    Args:
        batch01: ``(B, 3, H, W)`` float tensor in ``[0, 1]``.

    Returns:
        ``(B, 3, INPUT_SIZE, INPUT_SIZE)`` normalized tensor.
    """
    resized = torch.nn.functional.interpolate(
        batch01, size=(INPUT_SIZE, INPUT_SIZE), mode="bilinear", align_corners=False
    )
    mean = torch.tensor(IMAGENET_MEAN, device=batch01.device).view(1, 3, 1, 1)
    std = torch.tensor(IMAGENET_STD, device=batch01.device).view(1, 3, 1, 1)
    return (resized - mean) / std


def nt_xent(za: torch.Tensor, zb: torch.Tensor, temperature: float) -> torch.Tensor:
    """Symmetric NT-Xent (InfoNCE) loss over two aligned view batches.

    Args:
        za: ``(N, D)`` L2-normalized projections of view A (near-clean).
        zb: ``(N, D)`` L2-normalized projections of view B (heavy photo sim).
        temperature: Softmax temperature.

    Returns:
        The scalar loss.
    """
    n = za.shape[0]
    z = torch.cat([za, zb], dim=0)  # (2N, D)
    sim = z @ z.t() / temperature
    sim.fill_diagonal_(float("-inf"))
    targets = torch.cat(
        [torch.arange(n, 2 * n, device=z.device), torch.arange(0, n, device=z.device)]
    )
    return torch.nn.functional.cross_entropy(sim, targets)
