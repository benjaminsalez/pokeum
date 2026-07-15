<script setup lang="ts">
import {
  ArrowLeft,
  Camera,
  CameraOff,
  Check,
  Copy,
  Download,
  ScanLine,
  Trash2,
  Upload,
  X,
} from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";
import { cardImageUrl, health, identify, type IdentifyResponse } from "@/lib/api";
import { downloadFile, toPlainList, toTcgplayerCsv, type ScanEntry } from "@/lib/exporters";

type View = "scan" | "export";

const view = ref<View>("scan");
const video = ref<HTMLVideoElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const cameraOn = ref(false);
const scanning = ref(false);
const apiCards = ref<number | null>(null);
const pending = ref<IdentifyResponse | null>(null);
const flash = ref("");
const copied = ref(false);
const entries = ref<ScanEntry[]>([]);

let stream: MediaStream | null = null;
let flashTimer: number | undefined;

const totalCards = computed(() => entries.value.reduce((sum, e) => sum + e.quantity, 0));
const pendingMatch = computed(() => pending.value?.match ?? null);

onMounted(async () => {
  try {
    apiCards.value = (await health()).cards_indexed;
  } catch {
    apiCards.value = null;
  }
  await startCamera();
});

onBeforeUnmount(() => stopCamera());

async function startCamera(): Promise<void> {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    if (video.value) {
      video.value.srcObject = stream;
      await video.value.play();
      cameraOn.value = true;
    }
  } catch {
    cameraOn.value = false;
  }
}

function stopCamera(): void {
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  cameraOn.value = false;
}

function showFlash(text: string): void {
  flash.value = text;
  window.clearTimeout(flashTimer);
  flashTimer = window.setTimeout(() => (flash.value = ""), 2200);
}

function captureFrame(): Promise<Blob | null> {
  const element = video.value;
  if (!element || !cameraOn.value) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  canvas.width = element.videoWidth;
  canvas.height = element.videoHeight;
  canvas.getContext("2d")?.drawImage(element, 0, 0);
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/jpeg", 0.92));
}

async function scan(): Promise<void> {
  const blob = await captureFrame();
  if (!blob) {
    showFlash("Camera unavailable — use upload");
    return;
  }
  await identifyBlob(blob);
}

async function onUpload(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (file) await identifyBlob(file);
  if (fileInput.value) fileInput.value.value = "";
}

async function identifyBlob(blob: Blob): Promise<void> {
  scanning.value = true;
  pending.value = null;
  try {
    const result = await identify(blob);
    if (result.match && (result.status === "confident" || result.status === "uncertain")) {
      pending.value = result;
    } else {
      showFlash("No card recognized — try again");
    }
  } catch (error) {
    showFlash(error instanceof Error ? error.message : "Scan failed");
  } finally {
    scanning.value = false;
  }
}

function accept(): void {
  const match = pendingMatch.value;
  if (!match) return;
  const existing = entries.value.find((e) => e.card.card_id === match.card_id);
  if (existing) {
    existing.quantity += 1;
  } else {
    entries.value.unshift({ card: match, quantity: 1, scannedAt: new Date().toISOString() });
  }
  pending.value = null;
  showFlash(`Saved — ${totalCards.value} in list`);
}

function reject(): void {
  pending.value = null;
}

function removeEntry(cardId: string): void {
  entries.value = entries.value.filter((e) => e.card.card_id !== cardId);
}

function changeQuantity(cardId: string, delta: number): void {
  const entry = entries.value.find((e) => e.card.card_id === cardId);
  if (!entry) return;
  entry.quantity += delta;
  if (entry.quantity <= 0) removeEntry(cardId);
}

function exportCsv(): void {
  const stamp = new Date().toISOString().slice(0, 10);
  downloadFile(`pokeum-collection-${stamp}.csv`, toTcgplayerCsv(entries.value));
}

async function copyList(): Promise<void> {
  await navigator.clipboard.writeText(toPlainList(entries.value));
  copied.value = true;
  setTimeout(() => (copied.value = false), 1500);
}
</script>

<template>
  <!-- ============ SCAN VIEW: full-screen camera ============ -->
  <div v-if="view === 'scan'" class="fixed inset-0 bg-black">
    <video ref="video" autoplay playsinline muted class="absolute inset-0 h-full w-full object-cover" />

    <!-- No-camera fallback -->
    <div
      v-if="!cameraOn"
      class="absolute inset-0 flex flex-col items-center justify-center gap-3 text-white/70"
    >
      <CameraOff class="h-10 w-10" />
      <p class="text-sm">No camera — allow access or upload a photo</p>
      <div class="flex gap-2">
        <Button variant="secondary" size="sm" @click="startCamera">
          <Camera />
          Retry camera
        </Button>
        <Button variant="secondary" size="sm" @click="fileInput?.click()">
          <Upload />
          Upload
        </Button>
      </div>
    </div>

    <!-- Card guide -->
    <div v-if="cameraOn && !pendingMatch" class="pointer-events-none absolute inset-0 flex items-center justify-center">
      <div class="h-[70%] aspect-[63/88] rounded-xl border-2 border-white/50 shadow-[0_0_0_9999px_rgba(0,0,0,0.35)]" />
    </div>

    <!-- Top bar -->
    <div class="absolute inset-x-0 top-0 flex items-center justify-between p-4">
      <Badge variant="secondary" class="bg-black/50 text-white backdrop-blur">
        {{ apiCards === null ? "API offline" : `${totalCards} scanned` }}
      </Badge>
      <div class="flex gap-2">
        <Button variant="secondary" size="icon" class="bg-black/50 text-white backdrop-blur hover:bg-black/70" @click="fileInput?.click()">
          <Upload />
        </Button>
        <Button class="bg-white text-black hover:bg-white/90" @click="view = 'export'">
          Done
          <Badge v-if="entries.length" variant="secondary" class="ml-1">{{ totalCards }}</Badge>
        </Button>
      </div>
    </div>

    <!-- Flash message -->
    <div v-if="flash" class="absolute inset-x-0 top-20 flex justify-center">
      <Badge variant="secondary" class="bg-black/60 text-white backdrop-blur">{{ flash }}</Badge>
    </div>

    <!-- Scan button -->
    <div v-if="!pendingMatch" class="absolute inset-x-0 bottom-8 flex justify-center">
      <button
        class="flex h-20 w-20 items-center justify-center rounded-full border-4 border-white bg-white/20 text-white backdrop-blur transition active:scale-95 disabled:opacity-40"
        :disabled="scanning || !cameraOn"
        aria-label="Scan card"
        @click="scan"
      >
        <ScanLine class="h-8 w-8" :class="scanning ? 'animate-pulse' : ''" />
      </button>
    </div>

    <!-- Pending confirmation sheet: card visual left, details + actions right -->
    <div v-if="pendingMatch" class="absolute inset-x-0 bottom-0 p-4">
      <Card class="mx-auto flex max-w-md items-stretch gap-4 border-0 bg-background/95 p-4 backdrop-blur">
        <!-- Left: the card it thinks it is -->
        <img
          :src="cardImageUrl(pendingMatch.card_id)"
          :alt="pendingMatch.name"
          class="max-h-44 self-center rounded-lg border shadow-md"
          @error="($event.target as HTMLImageElement).style.display = 'none'"
        />
        <!-- Right: details on top, horizontal actions at the bottom -->
        <div class="flex min-w-0 flex-1 flex-col">
          <div class="min-w-0 flex-1 space-y-0.5">
            <p class="truncate font-semibold leading-tight">{{ pendingMatch.name }}</p>
            <p class="truncate text-xs text-muted-foreground">
              {{ pendingMatch.set.name }} · {{ pendingMatch.number }}
            </p>
            <div class="flex flex-wrap gap-1 pt-1">
              <Badge
                v-for="variant in (pendingMatch.variants ?? []).filter((v) => v.present)"
                :key="variant.kind"
                variant="outline"
              >
                {{ variant.kind.replace("_", " ") }}
              </Badge>
            </div>
          </div>
          <div class="flex gap-2 pt-3">
            <Button variant="outline" size="sm" class="flex-1" @click="reject">
              <X />
              Skip
            </Button>
            <Button size="sm" class="flex-1" @click="accept">
              <Check />
              Save
            </Button>
          </div>
        </div>
      </Card>
    </div>

    <input ref="fileInput" type="file" accept="image/*" class="hidden" @change="onUpload" />
  </div>

  <!-- ============ EXPORT VIEW ============ -->
  <div v-else class="min-h-screen bg-background">
    <header class="border-b">
      <div class="container flex h-14 items-center justify-between">
        <Button variant="ghost" size="sm" @click="view = 'scan'">
          <ArrowLeft />
          Keep scanning
        </Button>
        <Badge variant="outline">{{ totalCards }} card{{ totalCards === 1 ? "" : "s" }}</Badge>
      </div>
    </header>

    <main class="container max-w-2xl space-y-4 py-6">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold">Your scanned list</h1>
          <p class="text-sm text-muted-foreground">
            Export as TCGplayer-style CSV — importable in most collection trackers.
          </p>
        </div>
      </div>

      <Card v-if="!entries.length" class="p-10 text-center text-muted-foreground">
        Nothing saved yet — go scan some cards.
      </Card>

      <Card v-else>
        <ul class="divide-y">
          <li
            v-for="entry in entries"
            :key="entry.card.card_id"
            class="flex items-center justify-between gap-3 p-4"
          >
            <div class="min-w-0">
              <p class="truncate font-medium">{{ entry.card.name }}</p>
              <p class="truncate text-sm text-muted-foreground">
                {{ entry.card.set.name }} · {{ entry.card.number }}
                <span v-if="entry.card.set.code"> · {{ entry.card.set.code }}</span>
              </p>
            </div>
            <div class="flex items-center gap-1">
              <Button variant="outline" size="icon" class="h-8 w-8" @click="changeQuantity(entry.card.card_id, -1)">−</Button>
              <span class="w-8 text-center font-medium">{{ entry.quantity }}</span>
              <Button variant="outline" size="icon" class="h-8 w-8" @click="changeQuantity(entry.card.card_id, 1)">+</Button>
              <Button variant="ghost" size="icon" class="h-8 w-8" @click="removeEntry(entry.card.card_id)">
                <Trash2 class="h-4 w-4 text-muted-foreground" />
              </Button>
            </div>
          </li>
        </ul>
      </Card>

      <div class="flex gap-2">
        <Button class="flex-1" :disabled="!entries.length" @click="exportCsv">
          <Download />
          Export CSV
        </Button>
        <Button variant="outline" class="flex-1" :disabled="!entries.length" @click="copyList">
          <Copy />
          {{ copied ? "Copied!" : "Copy as text" }}
        </Button>
      </div>
    </main>
  </div>
</template>
