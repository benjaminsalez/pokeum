"""GPU photo-simulation augmentations in plain torch.

Everything here operates on a batched float image tensor ``(B, 3, H, W)`` in
``[0, 1]`` that already lives on the GPU, so the 16-vCPU box never becomes the
bottleneck: the CPU's only per-step job is shipping uint8 pixels.

Implemented without kornia on purpose — the box runs a bleeding-edge torch and
a third-party compatibility failure there would burn GPU rental time. The whole
stack is homographies via ``grid_sample``, analytic masks on coordinate grids,
and depthwise convolutions.

Random parameters are drawn on the CPU from an explicit ``torch.Generator`` and
moved to the device. That makes the eval augmentation bit-stable: the evaluator
reseeds one generator and processes the fixed query set in a fixed order with a
fixed batch size, so every epoch scores the exact same distorted queries.
"""

from __future__ import annotations

import logging

import torch
import torch.nn.functional as functional

from training.config import ARTWORK_BOX, AugConfig

logger = logging.getLogger(__name__)

_JPEG_PROBED = False
_JPEG_OK = False


def _uniform(
    shape: tuple[int, ...],
    low: float,
    high: float,
    gen: torch.Generator,
    device: torch.device,
) -> torch.Tensor:
    """Draw a uniform tensor on the generator's (CPU) device, then move it."""
    return (low + (high - low) * torch.rand(shape, generator=gen)).to(device)


def _bernoulli(batch: int, p: float, gen: torch.Generator, device: torch.device) -> torch.Tensor:
    """Draw a per-sample boolean mask with probability ``p``."""
    return (torch.rand((batch,), generator=gen) < p).to(device)


def _solve_homographies(src: torch.Tensor, dst: torch.Tensor) -> torch.Tensor:
    """Solve batched homographies mapping ``dst`` points to ``src`` points.

    Args:
        src: ``(B, 4, 2)`` source-quad corners (where to sample in the input).
        dst: ``(B, 4, 2)`` destination corners (the output unit square).

    Returns:
        ``(B, 3, 3)`` homography matrices H with ``H @ [x_dst, y_dst, 1] ~ src``.
    """
    batch = src.shape[0]
    rows = []
    for i in range(4):
        x, y = dst[:, i, 0], dst[:, i, 1]
        u, v = src[:, i, 0], src[:, i, 1]
        zeros = torch.zeros_like(x)
        ones = torch.ones_like(x)
        rows.append(torch.stack([x, y, ones, zeros, zeros, zeros, -u * x, -u * y], dim=1))
        rows.append(torch.stack([zeros, zeros, zeros, x, y, ones, -v * x, -v * y], dim=1))
    a_mat = torch.stack(rows, dim=1)  # (B, 8, 8)
    b_vec = torch.stack([src[:, i // 2, i % 2] for i in range(8)], dim=1)  # u0,v0,u1,v1...
    h8 = torch.linalg.solve(a_mat, b_vec)
    h9 = torch.cat([h8, torch.ones((batch, 1), device=src.device, dtype=src.dtype)], dim=1)
    return h9.view(batch, 3, 3)


def perspective_warp(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Simulate imperfect rectification: perspective + rotation + shift + scale.

    Args:
        x: Batched image tensor ``(B, 3, H, W)`` in ``[0, 1]``.
        cfg: Augmentation ranges.
        gen: CPU random generator.

    Returns:
        The warped batch (border-replicated padding, matching rectified photos).
    """
    batch, _, height, width = x.shape
    device = x.device
    apply = _bernoulli(batch, cfg.perspective_p, gen, device)

    # Base corners in normalized [-1, 1] coords: TL, TR, BR, BL.
    base = torch.tensor([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]], device=device).expand(
        batch, 4, 2
    )

    angle = _uniform((batch,), -cfg.rotate_deg, cfg.rotate_deg, gen, device) * torch.pi / 180
    scale = _uniform((batch,), cfg.scale_range[0], cfg.scale_range[1], gen, device)
    shift = _uniform((batch, 1, 2), -cfg.translate_frac, cfg.translate_frac, gen, device) * 2
    jitter = _uniform((batch, 4, 2), -cfg.corner_jitter, cfg.corner_jitter, gen, device) * 2

    cos, sin = torch.cos(angle), torch.sin(angle)
    rot = torch.stack(
        [torch.stack([cos, -sin], dim=1), torch.stack([sin, cos], dim=1)], dim=1
    )  # (B, 2, 2)
    src = base @ rot.transpose(1, 2) / scale.view(batch, 1, 1) + shift + jitter

    keep = ~apply.view(batch, 1, 1)
    src = torch.where(keep, base, src)
    homography = _solve_homographies(src, base)

    ys = torch.linspace(-1, 1, height, device=device)
    xs = torch.linspace(-1, 1, width, device=device)
    grid_y, grid_x = torch.meshgrid(ys, xs, indexing="ij")
    ones = torch.ones_like(grid_x)
    pts = torch.stack([grid_x, grid_y, ones], dim=-1).view(1, -1, 3).expand(batch, -1, 3)
    mapped = pts @ homography.transpose(1, 2)
    mapped = mapped[..., :2] / mapped[..., 2:3].clamp(min=1e-6)
    grid = mapped.view(batch, height, width, 2)
    return functional.grid_sample(
        x, grid, mode="bilinear", padding_mode="border", align_corners=True
    )


def color_jitter(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Brightness, contrast, white balance, saturation, and gamma shifts."""
    batch = x.shape[0]
    device = x.device
    apply = _bernoulli(batch, cfg.color_p, gen, device).view(batch, 1, 1, 1).float()

    brightness = _uniform((batch, 1, 1, 1), *cfg.brightness_range, gen, device)
    contrast = _uniform((batch, 1, 1, 1), *cfg.contrast_range, gen, device)
    wb = _uniform((batch, 3, 1, 1), *cfg.wb_gain_range, gen, device)
    saturation = _uniform((batch, 1, 1, 1), *cfg.saturation_range, gen, device)
    gamma = _uniform((batch, 1, 1, 1), *cfg.gamma_range, gen, device)

    out = x * brightness * wb
    mean = out.mean(dim=(2, 3), keepdim=True)
    out = (out - mean) * contrast + mean
    gray = out.mean(dim=1, keepdim=True)
    out = gray + (out - gray) * saturation
    out = out.clamp(1e-4, 1.0) ** gamma
    return (x * (1 - apply) + out.clamp(0, 1) * apply).clamp(0, 1)


def _coord_grid(
    batch: int, height: int, width: int, device: torch.device
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return normalized [0, 1] coordinate grids ``(B, H, W)`` for y and x."""
    ys = torch.linspace(0, 1, height, device=device)
    xs = torch.linspace(0, 1, width, device=device)
    grid_y, grid_x = torch.meshgrid(ys, xs, indexing="ij")
    return grid_y.expand(batch, -1, -1), grid_x.expand(batch, -1, -1)


def elliptical_glare(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Add 1-2 anisotropic Gaussian glare blobs via screen blending."""
    batch, _, height, width = x.shape
    device = x.device
    out = x
    for _ in range(2):
        apply = _bernoulli(batch, cfg.glare_p * 0.7, gen, device).view(batch, 1, 1)
        cy = _uniform((batch, 1, 1), 0.0, 1.0, gen, device)
        cx = _uniform((batch, 1, 1), 0.0, 1.0, gen, device)
        sig_a = _uniform((batch, 1, 1), *cfg.glare_sigma_frac, gen, device)
        sig_b = _uniform((batch, 1, 1), *cfg.glare_sigma_frac, gen, device)
        theta = _uniform((batch, 1, 1), 0.0, 3.14159, gen, device)
        peak = _uniform((batch, 1, 1), *cfg.glare_peak, gen, device)

        grid_y, grid_x = _coord_grid(batch, height, width, device)
        dy, dx = grid_y - cy, grid_x - cx
        cos, sin = torch.cos(theta), torch.sin(theta)
        u = dx * cos + dy * sin
        v = -dx * sin + dy * cos
        blob = torch.exp(-0.5 * ((u / sig_a) ** 2 + (v / sig_b) ** 2)) * peak * apply
        blob = blob.unsqueeze(1)  # (B, 1, H, W)
        out = 1 - (1 - out) * (1 - blob)  # screen blend
    return out.clamp(0, 1)


def band_glare(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Add a soft linear glare band (sleeve reflection) via screen blending."""
    batch, _, height, width = x.shape
    device = x.device
    apply = _bernoulli(batch, cfg.band_p, gen, device).view(batch, 1, 1)
    center = _uniform((batch, 1, 1), 0.1, 0.9, gen, device)
    half_width = _uniform((batch, 1, 1), *cfg.band_width_frac, gen, device) / 2
    angle = _uniform((batch, 1, 1), -0.52, 0.52, gen, device)  # +/-30 degrees
    intensity = _uniform((batch, 1, 1), *cfg.band_intensity, gen, device)

    grid_y, grid_x = _coord_grid(batch, height, width, device)
    # Signed distance of each pixel to the band's center line.
    dist = (grid_y - center) * torch.cos(angle) + (grid_x - 0.5) * torch.sin(angle)
    edge0, edge1 = half_width, half_width * 1.5
    t = ((edge1 - dist.abs()) / (edge1 - edge0).clamp(min=1e-6)).clamp(0, 1)
    band = t * t * (3 - 2 * t) * intensity * apply
    return (1 - (1 - x) * (1 - band.unsqueeze(1))).clamp(0, 1)


def _depthwise_blur(x: torch.Tensor, kernels: torch.Tensor) -> torch.Tensor:
    """Convolve each image with its own 2-D kernel via grouped conv.

    Args:
        x: ``(B, 3, H, W)`` batch.
        kernels: ``(B, k, k)`` normalized kernels.
    """
    batch, channels, height, width = x.shape
    k = kernels.shape[-1]
    weight = kernels.unsqueeze(1).repeat_interleave(channels, dim=0)  # (B*3, 1, k, k)
    folded = x.reshape(1, batch * channels, height, width)
    blurred = functional.conv2d(folded, weight, padding=k // 2, groups=batch * channels)
    return blurred.reshape(batch, channels, height, width)


def blur(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Apply per-sample gaussian OR motion blur (or none)."""
    batch = x.shape[0]
    device = x.device
    choice = torch.rand((batch,), generator=gen).to(device)
    gauss = choice < cfg.blur_gaussian_p
    motion = (choice >= cfg.blur_gaussian_p) & (choice < cfg.blur_gaussian_p + cfg.blur_motion_p)

    k = 15
    coords = torch.arange(k, device=device, dtype=torch.float32) - k // 2
    # Gaussian kernels.
    sigma = _uniform((batch, 1), *cfg.blur_sigma, gen, device)
    g1d = torch.exp(-0.5 * (coords.unsqueeze(0) / sigma) ** 2)
    gauss_k = g1d.unsqueeze(2) * g1d.unsqueeze(1)
    # Motion kernels: a soft line at a random angle with random length.
    length = _uniform(
        (batch, 1, 1), float(cfg.motion_len[0]), float(cfg.motion_len[1]), gen, device
    )
    theta = _uniform((batch, 1, 1), 0.0, 3.14159, gen, device)
    yy = coords.view(1, k, 1).expand(batch, k, k)
    xx = coords.view(1, 1, k).expand(batch, k, k)
    along = xx * torch.cos(theta) + yy * torch.sin(theta)
    across = -xx * torch.sin(theta) + yy * torch.cos(theta)
    motion_k = ((along.abs() <= length / 2) & (across.abs() <= 0.8)).float()

    identity = torch.zeros((k, k), device=device)
    identity[k // 2, k // 2] = 1.0
    kernels = torch.where(
        gauss.view(batch, 1, 1),
        gauss_k,
        torch.where(motion.view(batch, 1, 1), motion_k, identity.expand(batch, k, k)),
    )
    kernels = kernels / kernels.sum(dim=(1, 2), keepdim=True).clamp(min=1e-6)
    return _depthwise_blur(x, kernels)


def sensor_noise(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Add per-sample gaussian sensor noise."""
    batch = x.shape[0]
    device = x.device
    apply = _bernoulli(batch, cfg.noise_p, gen, device).view(batch, 1, 1, 1).float()
    sigma = _uniform((batch, 1, 1, 1), *cfg.noise_sigma, gen, device)
    noise = torch.randn(x.shape, generator=gen).to(device) * sigma
    return (x + noise * apply).clamp(0, 1)


def print_mottle(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Multiply by low-frequency mottle simulating print wear / uneven light."""
    batch, _, height, width = x.shape
    device = x.device
    apply = _bernoulli(batch, cfg.mottle_p, gen, device).view(batch, 1, 1, 1).float()
    coarse = _uniform((batch, 1, 8, 8), -cfg.mottle_amp, cfg.mottle_amp, gen, device)
    field = functional.interpolate(
        coarse, size=(height, width), mode="bilinear", align_corners=False
    )
    return (x * (1 + field * apply)).clamp(0, 1)


def jpeg_artifacts(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Round-trip a random subset through JPEG on the GPU (nvJPEG), if available."""
    global _JPEG_PROBED, _JPEG_OK
    if not _JPEG_PROBED:
        _JPEG_PROBED = True
        try:
            from torchvision.io import decode_jpeg, encode_jpeg

            probe = (torch.rand(3, 32, 32, device=x.device) * 255).to(torch.uint8)
            decode_jpeg(encode_jpeg(probe, quality=80), device=x.device)
            _JPEG_OK = True
        except Exception as error:  # noqa: BLE001 - optional aug, never fatal
            logger.warning("CUDA JPEG unavailable (%s); skipping JPEG augmentation", error)
    if not _JPEG_OK:
        return x

    from torchvision.io import decode_jpeg, encode_jpeg

    batch = x.shape[0]
    apply = torch.rand((batch,), generator=gen) < cfg.jpeg_p
    quality = torch.randint(cfg.jpeg_quality[0], cfg.jpeg_quality[1] + 1, (batch,), generator=gen)
    out = x.clone()
    for i in torch.nonzero(apply).flatten().tolist():
        img8 = (x[i] * 255).to(torch.uint8)
        out[i] = (
            decode_jpeg(encode_jpeg(img8, quality=int(quality[i])), device=x.device).float() / 255
        )
    return out


def finger_occlusion(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Paste 1-2 soft skin-tone blobs anchored to a card edge."""
    batch, _, height, width = x.shape
    device = x.device
    out = x
    for _ in range(2):
        apply = _bernoulli(batch, cfg.occlude_p * 0.7, gen, device).view(batch, 1, 1)
        # Anchor on a random edge: clamp one coordinate to {0, 1}.
        cy = _uniform((batch, 1, 1), 0.0, 1.0, gen, device)
        cx = _uniform((batch, 1, 1), 0.0, 1.0, gen, device)
        edge_pick = torch.rand((batch, 1, 1), generator=gen).to(device)
        cy = torch.where(edge_pick < 0.25, torch.zeros_like(cy), cy)
        cy = torch.where((edge_pick >= 0.25) & (edge_pick < 0.5), torch.ones_like(cy), cy)
        cx = torch.where((edge_pick >= 0.5) & (edge_pick < 0.75), torch.zeros_like(cx), cx)
        cx = torch.where(edge_pick >= 0.75, torch.ones_like(cx), cx)

        area = _uniform((batch, 1, 1), *cfg.occlude_area, gen, device)
        radius = (area / 3.14159).sqrt()  # circle of the drawn area (fractional units)
        grid_y, grid_x = _coord_grid(batch, height, width, device)
        dist = ((grid_y - cy) ** 2 + ((grid_x - cx) * width / height) ** 2).sqrt()
        alpha = ((radius * 1.2 - dist) / (radius * 0.4).clamp(min=1e-6)).clamp(0, 1) * apply

        skin_r = _uniform((batch, 1, 1), 0.55, 0.85, gen, device)
        skin_g = skin_r * _uniform((batch, 1, 1), 0.65, 0.8, gen, device)
        skin_b = skin_g * _uniform((batch, 1, 1), 0.75, 0.9, gen, device)
        skin = torch.stack([skin_r, skin_g, skin_b], dim=1)  # (B, 3, 1, 1) after view
        skin = (
            skin.view(batch, 3, 1, 1)
            + torch.randn((batch, 3, height, width), generator=gen).to(device) * 0.02
        )
        out = out * (1 - alpha.unsqueeze(1)) + skin.clamp(0, 1) * alpha.unsqueeze(1)
    return out.clamp(0, 1)


def heavy_view(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Full photo-simulation pipeline for the query-side view.

    Args:
        x: Batched card images ``(B, 3, H, W)`` float in ``[0, 1]`` on GPU.
        cfg: Augmentation configuration.
        gen: CPU random generator (seeded for deterministic eval draws).

    Returns:
        The augmented batch, same shape, clamped to ``[0, 1]``.
    """
    x = perspective_warp(x, cfg, gen)
    x = color_jitter(x, cfg, gen)
    x = elliptical_glare(x, cfg, gen)
    x = band_glare(x, cfg, gen)
    x = blur(x, cfg, gen)
    x = sensor_noise(x, cfg, gen)
    x = print_mottle(x, cfg, gen)
    x = jpeg_artifacts(x, cfg, gen)
    x = finger_occlusion(x, cfg, gen)
    return x


def clean_view(x: torch.Tensor, cfg: AugConfig, gen: torch.Generator) -> torch.Tensor:
    """Near-clean gallery-side view: tiny scale and brightness jitter only."""
    batch = x.shape[0]
    device = x.device
    scale_apply = _bernoulli(batch, 0.5, gen, device)
    scale = torch.where(
        scale_apply,
        _uniform((batch,), *cfg.clean_scale, gen, device),
        torch.ones(batch, device=device),
    )
    theta = torch.zeros((batch, 2, 3), device=device)
    theta[:, 0, 0] = 1.0 / scale
    theta[:, 1, 1] = 1.0 / scale
    grid = functional.affine_grid(theta, list(x.shape), align_corners=False)
    out = functional.grid_sample(
        x, grid, mode="bilinear", padding_mode="border", align_corners=False
    )

    bright_apply = _bernoulli(batch, 0.5, gen, device).view(batch, 1, 1, 1).float()
    brightness = _uniform((batch, 1, 1, 1), *cfg.clean_brightness, gen, device)
    return (out * (brightness * bright_apply + (1 - bright_apply))).clamp(0, 1)


def artwork_crop(x: torch.Tensor, jitter: float, gen: torch.Generator) -> torch.Tensor:
    """Crop the artwork window from batched cards, with optional edge jitter.

    Args:
        x: Batched card images ``(B, 3, H, W)``.
        jitter: Max fractional jitter added to each crop edge (0 for exact).
        gen: CPU random generator.

    Returns:
        The cropped batch resized back to the input's spatial size.
    """
    batch, _, height, width = x.shape
    device = x.device
    left, top, right, bottom = ARTWORK_BOX
    box = torch.tensor([left, top, right, bottom], device=device).expand(batch, 4).clone()
    if jitter > 0:
        box = box + _uniform((batch, 4), -jitter, jitter, gen, device)
    box = box.clamp(0.0, 1.0)

    # Affine grid mapping the output square onto the (jittered) crop box.
    cx = (box[:, 0] + box[:, 2]) / 2 * 2 - 1
    cy = (box[:, 1] + box[:, 3]) / 2 * 2 - 1
    sx = (box[:, 2] - box[:, 0]).clamp(min=0.05)
    sy = (box[:, 3] - box[:, 1]).clamp(min=0.05)
    theta = torch.zeros((batch, 2, 3), device=device)
    theta[:, 0, 0] = sx
    theta[:, 1, 1] = sy
    theta[:, 0, 2] = cx
    theta[:, 1, 2] = cy
    grid = functional.affine_grid(theta, [batch, 3, height, width], align_corners=False)
    return functional.grid_sample(
        x, grid, mode="bilinear", padding_mode="border", align_corners=False
    )
