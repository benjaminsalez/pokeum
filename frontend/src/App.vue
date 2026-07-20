<script setup lang="ts">
import {
  ArrowLeft,
  CameraOff,
  Check,
  Download,
  ImagePlus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import DataNotice from "@/components/DataNotice.vue";
import PokeballButton from "@/components/scanner/PokeballButton.vue";
import ScannerFrame from "@/components/scanner/ScannerFrame.vue";
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";
import {
  cardArtUrl,
  identify,
  SCAN_ANNOTATION_SCHEMA_VERSION,
  submitScan,
  type IdentifyResponse,
} from "@/lib/api";
import {
  clearCollection,
  loadCollection,
  requestPersistentStorage,
  saveCollection,
} from "@/lib/collection";
import { downloadFile, toTcgplayerCsv, type ScanEntry } from "@/lib/exporters";
import { GUIDE_CROP_MARGIN, downscaleForUpload, guideCropSourceRect } from "@/lib/image";
import { hasSeenNotice, markNoticeSeen } from "@/lib/notice";

type View = "scan" | "export";
type CameraState = "starting" | "ready" | "unavailable";

const view = ref<View>("scan");
const video = ref<HTMLVideoElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const scannerFrame = ref<InstanceType<typeof ScannerFrame> | null>(null);
const cameraState = ref<CameraState>("starting");
const scanning = ref(false);
const pending = ref<IdentifyResponse | null>(null);
const pendingBlob = ref<Blob | null>(null);
const flash = ref("");
const entries = ref<ScanEntry[]>([]);
const showNotice = ref(!hasSeenNotice());

let stream: MediaStream | null = null;
let flashTimer: number | undefined;
let saveTimer: number | undefined;
let persistenceRequested = false;

const totalCards = computed(() => entries.value.reduce((sum, entry) => sum + entry.quantity, 0));
const collectionCards = computed(() =>
  entries.value.flatMap((entry) =>
    Array.from({ length: entry.quantity }, (_, index) => ({
      card: entry.card,
      key: `${entry.card.card_id}-${index}`,
    })),
  ),
);
const pendingMatch = computed(() => pending.value?.match ?? null);
const isCameraReady = computed(() => cameraState.value === "ready");

onMounted(() => {
  entries.value = loadCollection();
  document.addEventListener("visibilitychange", flushCollectionSave);
  void startCamera();
});

onBeforeUnmount(() => {
  stopCamera();
  window.clearTimeout(flashTimer);
  document.removeEventListener("visibilitychange", flushCollectionSave);
  flushCollectionSave();
});

// Debounced persistence: accepting a scan mutates entries several times in a
// burst (quantity bumps, unshift), one write 400 ms later covers them all.
watch(
  entries,
  () => {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(persistCollection, 400);
    if (!persistenceRequested && entries.value.length > 0) {
      persistenceRequested = true;
      void requestPersistentStorage();
    }
  },
  { deep: true },
);

function persistCollection(): void {
  window.clearTimeout(saveTimer);
  saveTimer = undefined;
  saveCollection(entries.value);
}

// Mobile PWAs are killed without unload events; the hidden transition is the
// last reliable moment to flush a pending debounced write.
function flushCollectionSave(): void {
  if (document.visibilityState === "hidden" || saveTimer !== undefined) {
    persistCollection();
  }
}

function haptic(pattern: number | number[]): void {
  navigator.vibrate?.(pattern);
}

async function startCamera(): Promise<void> {
  stopCamera();
  cameraState.value = "starting";

  if (!navigator.mediaDevices?.getUserMedia) {
    cameraState.value = "unavailable";
    return;
  }

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    });
    if (!video.value) {
      stopCamera();
      cameraState.value = "unavailable";
      return;
    }
    video.value.srcObject = stream;
    await video.value.play();
    cameraState.value = "ready";
  } catch {
    stopCamera();
    cameraState.value = "unavailable";
  }
}

function stopCamera(): void {
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  if (video.value) video.value.srcObject = null;
}

async function setView(nextView: View): Promise<void> {
  view.value = nextView;
  pending.value = null;
  pendingBlob.value = null;
  if (nextView === "export") {
    stopCamera();
    cameraState.value = "starting";
    return;
  }
  await nextTick();
  await startCamera();
}

function showFlash(text: string): void {
  flash.value = text;
  window.clearTimeout(flashTimer);
  flashTimer = window.setTimeout(() => {
    flash.value = "";
  }, 2400);
}

function captureFrame(): Promise<Blob | null> {
  const element = video.value;
  if (!element || !isCameraReady.value || element.videoWidth === 0) {
    return Promise.resolve(null);
  }
  // Crop to the on-screen guide frame (plus margin) so the recognizer gets
  // mostly-card pixels instead of the whole scene; full frame as fallback.
  const frameRect = scannerFrame.value?.getRect() ?? null;
  const crop = frameRect
    ? guideCropSourceRect(
        element.getBoundingClientRect(),
        frameRect,
        element.videoWidth,
        element.videoHeight,
      )
    : null;
  const source = crop ?? { x: 0, y: 0, width: element.videoWidth, height: element.videoHeight };
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(source.width);
  canvas.height = Math.round(source.height);
  canvas
    .getContext("2d")
    ?.drawImage(
      element,
      source.x,
      source.y,
      source.width,
      source.height,
      0,
      0,
      canvas.width,
      canvas.height,
    );
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.92);
  });
}

async function scan(): Promise<void> {
  haptic(12);
  const blob = await captureFrame();
  if (!blob) {
    showFlash("Camera unavailable — upload a photo instead");
    return;
  }
  // Quad rectification remains preferred, but the server can recover this
  // known guide region when glare or a low-contrast background breaks its edge.
  await identifyBlob(blob, false, GUIDE_CROP_MARGIN);
}

async function onUpload(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (file) {
    haptic(10);
    await identifyBlob(await downscaleForUpload(file));
  }
  if (fileInput.value) fileInput.value.value = "";
}

async function identifyBlob(
  blob: Blob,
  requireDetection = false,
  guideMargin?: number,
): Promise<void> {
  if (scanning.value) return;

  scanning.value = true;
  pending.value = null;
  pendingBlob.value = null;

  try {
    const result = await identify(blob, 5, requireDetection, guideMargin);
    if (result.match && (result.status === "confident" || result.status === "uncertain")) {
      pending.value = result;
      pendingBlob.value = blob;
      haptic([14, 45, 22]);
    } else {
      haptic([18, 50, 18]);
      showFlash(
        result.status === "no_card_detected"
          ? "No card found — center it in the frame"
          : "No match yet — try a steadier angle",
      );
    }
  } catch (error) {
    haptic([20, 60, 20]);
    showFlash(error instanceof Error ? error.message : "Scan failed — please try again");
  } finally {
    scanning.value = false;
  }
}

function accept(): void {
  const match = pendingMatch.value;
  if (!match) return;
  const existing = entries.value.find((entry) => entry.card.card_id === match.card_id);
  if (existing) {
    existing.quantity += 1;
  } else {
    entries.value.unshift({ card: match, quantity: 1, scannedAt: new Date().toISOString() });
  }
  collectScan();
  pending.value = null;
  pendingBlob.value = null;
  haptic([12, 35, 18]);
  showFlash(`Saved to collection · ${totalCards.value} total`);
}

/** Fire-and-forget upload of the accepted scan — best-effort, never blocking. */
function collectScan(): void {
  const match = pendingMatch.value;
  const blob = pendingBlob.value;
  const result = pending.value;
  if (!match || !blob || !result) return;
  void submitScan(blob, {
    schema_version: SCAN_ANNOTATION_SCHEMA_VERSION,
    consent: true,
    card_id: match.card_id,
    set_id: match.set.id,
    number: match.number,
    status: result.status,
    variants: match.variants ?? [],
    alternate_card_ids: result.alternates.map((alt) => alt.card_id),
    captured_at: new Date().toISOString(),
  }).catch(() => {
    // Collection is best-effort; a failed upload must never surface to the user.
  });
}

function dismissNotice(): void {
  markNoticeSeen();
  showNotice.value = false;
  haptic(10);
}

function reject(): void {
  pending.value = null;
  pendingBlob.value = null;
  haptic(8);
}

function exportCsv(): void {
  const stamp = new Date().toISOString().slice(0, 10);
  downloadFile(`pokeum-collection-${stamp}.csv`, toTcgplayerCsv(entries.value));
  haptic(12);
}

function clearAll(): void {
  if (!window.confirm("Clear the whole collection? This cannot be undone.")) return;
  entries.value = [];
  clearCollection();
  haptic(12);
}

</script>

<template>
  <div
    v-if="view === 'scan'"
    class="fixed left-0 top-0 isolate h-svh w-full overflow-hidden bg-zinc-950 text-white"
  >
    <video
      ref="video"
      autoplay
      playsinline
      muted
      class="absolute inset-0 -z-[4] h-full w-full scale-[1.015] object-cover opacity-0 transition-opacity duration-300 motion-reduce:transition-none"
      :class="isCameraReady ? 'opacity-100' : ''"
    />
    <div
      class="pointer-events-none absolute inset-0 -z-[3] bg-[linear-gradient(to_bottom,rgba(0,0,0,0.54),transparent_20%,transparent_72%,rgba(0,0,0,0.76)),radial-gradient(circle_at_50%_50%,transparent_38%,rgba(0,0,0,0.22)_100%)]"
    />
    <div
      class="pointer-events-none absolute inset-0 -z-[2] shadow-[inset_0_0_9rem_rgba(0,0,0,0.32)]"
    />

    <header
      class="absolute inset-x-0 top-0 z-20 flex items-center justify-between gap-3 pb-3 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] pt-[max(0.8rem,env(safe-area-inset-top))] sm:px-6"
    >
      <span aria-hidden="true" />

      <button
        type="button"
        class="inline-flex min-h-11 touch-manipulation items-center justify-center gap-2 rounded-lg border border-white/10 bg-zinc-950/60 px-3 text-xs font-semibold text-white shadow-lg backdrop-blur-xl transition active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
        @click="setView('export')"
      >
        <span>Collection</span>
        <span
          v-if="totalCards"
          class="grid h-6 min-w-6 place-items-center rounded-md bg-white px-1 text-[0.7rem] font-extrabold text-zinc-900"
        >
          {{ totalCards }}
        </span>
      </button>
    </header>

    <div
      v-if="cameraState === 'unavailable'"
      class="absolute left-1/2 top-1/2 z-10 flex w-[min(calc(100%_-_2rem),25rem)] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-3 rounded-xl border border-white/10 bg-zinc-900/80 p-6 text-center shadow-2xl backdrop-blur-2xl"
    >
      <div
        aria-hidden="true"
        class="grid h-16 w-16 place-items-center rounded-full border border-white/10 bg-white/[0.07]"
      >
        <CameraOff class="h-6 w-6 text-white/70" />
      </div>
      <p class="mt-1 text-base font-bold tracking-tight">Camera is unavailable</p>
      <p class="max-w-sm text-sm leading-relaxed text-white/60">
        Allow camera access in your browser, or choose a clear photo from your library.
      </p>
      <div class="flex w-full gap-2.5 pt-1">
        <Button variant="secondary" size="lg" class="min-h-12 flex-1" @click="startCamera">
          <RefreshCw />
          Retry
        </Button>
        <Button size="lg" class="min-h-12 flex-1" @click="fileInput?.click()">
          <ImagePlus />
          Choose photo
        </Button>
      </div>
    </div>

    <div
      v-if="scanning && !isCameraReady"
      role="status"
      class="absolute left-1/2 top-1/2 z-20 h-20 w-28 -translate-x-1/2 -translate-y-1/2 overflow-hidden"
    >
      <div
        class="absolute inset-x-2 top-[4%] h-px animate-scanner-sweep bg-sky-300 shadow-[0_0_6px_1px_rgba(125,211,252,0.65)] motion-reduce:top-1/2 motion-reduce:animate-none"
      />
    </div>

    <div
      v-if="isCameraReady && !pendingMatch"
      class="pointer-events-none absolute inset-x-0 bottom-[max(9.8rem,calc(env(safe-area-inset-bottom)+8.8rem))] top-[max(4.5rem,calc(env(safe-area-inset-top)+3.6rem))] grid place-items-center [@media(max-height:700px)]:bottom-[max(7.4rem,calc(env(safe-area-inset-bottom)+6.8rem))] [@media(max-height:700px)]:top-[max(3.8rem,calc(env(safe-area-inset-top)+3.1rem))]"
    >
      <ScannerFrame ref="scannerFrame" :scanning="scanning" />
    </div>

    <Transition
      enter-active-class="transition duration-200 ease-out motion-reduce:transition-none"
      enter-from-class="-translate-y-2 scale-95 opacity-0"
      leave-active-class="transition duration-150 ease-in motion-reduce:transition-none"
      leave-to-class="-translate-y-2 scale-95 opacity-0"
    >
      <div
        v-if="flash"
        role="status"
        aria-live="polite"
        class="pointer-events-none absolute inset-x-0 top-[max(4.8rem,calc(env(safe-area-inset-top)+3.8rem))] z-50 flex justify-center px-4"
      >
        <div
          class="flex min-h-10 items-center gap-2 rounded-lg border border-white/10 bg-zinc-950/80 px-3.5 py-2 text-xs font-semibold text-white shadow-xl backdrop-blur-xl"
        >
          <Check v-if="flash.startsWith('Saved')" class="h-4 w-4 text-green-400" />
          <span>{{ flash }}</span>
        </div>
      </div>
    </Transition>

    <div
      v-if="isCameraReady && !pendingMatch"
      class="absolute inset-x-0 bottom-[max(1rem,env(safe-area-inset-bottom))] z-20 flex justify-center bg-gradient-to-t from-black/60 to-transparent pt-10"
    >
      <PokeballButton :disabled="scanning" :scanning="scanning" @click="scan" />
    </div>

    <Transition
      enter-active-class="transition duration-200 ease-out motion-reduce:transition-none"
      enter-from-class="opacity-0"
      leave-active-class="transition duration-150 ease-in motion-reduce:transition-none"
      leave-to-class="opacity-0"
    >
      <div
        v-if="pendingMatch"
        class="absolute inset-0 z-40 flex items-end justify-center pb-[max(1rem,env(safe-area-inset-bottom))]"
      >
        <button
          type="button"
          class="absolute inset-0 border-0 bg-black/45 backdrop-blur-[4px]"
          aria-label="Dismiss match"
          @click="reject"
        />
        <section
          aria-labelledby="match-title"
          class="relative z-10 max-h-[calc(100svh_-_2rem)] w-[calc(100%_-_1.5rem)] max-w-lg overflow-y-auto overscroll-contain rounded-xl border border-white/50 bg-zinc-50/95 p-4 text-zinc-900 shadow-[0_-16px_60px_rgba(0,0,0,0.28)] backdrop-blur-2xl"
        >
          <div
            class="grid grid-cols-[6.2rem_minmax(0,1fr)] items-stretch gap-4 [@media(max-height:700px)]:grid-cols-[5.3rem_minmax(0,1fr)]"
          >
            <div
              class="relative min-h-[8.4rem] overflow-hidden rounded-lg bg-zinc-100 shadow-md [@media(max-height:700px)]:min-h-[7.2rem]"
            >
              <img
                :src="cardArtUrl(pendingMatch)"
                :alt="pendingMatch.name"
                class="h-full w-full object-cover"
                @error="($event.target as HTMLImageElement).style.display = 'none'"
              />
              <span
                aria-hidden="true"
                class="pointer-events-none absolute inset-0 -translate-x-[120%] animate-card-shine bg-[linear-gradient(120deg,transparent_25%,rgba(255,255,255,0.28)_42%,transparent_58%)] motion-reduce:animate-none"
              />
            </div>

            <div class="flex min-w-0 flex-col justify-between py-1 pr-1">
              <div>
                <h2
                  id="match-title"
                  class="truncate text-lg font-extrabold leading-tight tracking-tight"
                >
                  {{ pendingMatch.name }}
                </h2>
                <p class="mt-1 truncate text-xs text-zinc-600">{{ pendingMatch.set.name }}</p>
                <p class="mt-0.5 truncate text-[0.7rem] font-semibold text-zinc-400">
                  {{ pendingMatch.set.code ? `${pendingMatch.set.code} · ` : "" }}{{ pendingMatch.number }}
                </p>
                <p
                  v-if="(pendingMatch.variants ?? []).some((variant) => variant.present)"
                  class="mt-2 text-[0.7rem] capitalize leading-relaxed text-zinc-500"
                >
                  {{
                    (pendingMatch.variants ?? [])
                      .filter((variant) => variant.present)
                      .map((variant) => variant.kind.replaceAll("_", " "))
                      .join(" · ")
                  }}
                </p>
              </div>
            </div>
          </div>

          <div class="flex gap-2.5 pt-3">
            <Button variant="outline" size="lg" class="min-h-12 flex-1" @click="reject">
              <X />
              Not this one
            </Button>
            <Button
              size="lg"
              class="min-h-12 flex-[1.2] shadow-[0_8px_20px_rgba(0,0,0,0.16)]"
              @click="accept"
            >
              <Check />
              Save card
            </Button>
          </div>
        </section>
      </div>
    </Transition>

    <input
      ref="fileInput"
      type="file"
      accept="image/*"
      class="sr-only"
      @change="onUpload"
    />
  </div>

  <div v-else class="min-h-svh bg-background">
    <Button
      variant="outline"
      size="icon"
      class="fixed left-[max(0.75rem,env(safe-area-inset-left))] top-[max(0.75rem,env(safe-area-inset-top))] z-20 rounded-md bg-background/90 shadow-md backdrop-blur"
      aria-label="Back to scanner"
      @click="setView('scan')"
    >
      <ArrowLeft />
    </Button>

    <main
      class="grid grid-cols-2 gap-2 p-2 pb-24 pt-[calc(max(0.75rem,env(safe-area-inset-top))_+_3.5rem)] sm:grid-cols-3 sm:gap-3 sm:p-3 sm:pb-24 md:grid-cols-4 xl:grid-cols-5"
    >
      <Card
        v-for="item in collectionCards"
        :key="item.key"
        class="aspect-[63/88] overflow-hidden rounded-lg border-0 bg-muted shadow-none"
      >
        <img
          :src="cardArtUrl(item.card)"
          :alt="item.card.name"
          class="h-full w-full object-cover"
          loading="lazy"
          @error="($event.target as HTMLImageElement).style.visibility = 'hidden'"
        />
      </Card>
    </main>

    <div
      class="fixed bottom-[max(0.75rem,env(safe-area-inset-bottom))] left-3 right-3 z-20 rounded-lg border bg-background/95 p-3 shadow-lg backdrop-blur"
    >
      <div class="mx-auto flex w-full max-w-md gap-2">
        <Button
          size="lg"
          class="min-h-12 flex-1 rounded-md"
          :disabled="!entries.length"
          @click="exportCsv"
        >
          <Download />
          Export CSV
        </Button>
        <Button
          variant="outline"
          size="lg"
          class="min-h-12 rounded-md"
          :disabled="!entries.length"
          aria-label="Clear collection"
          @click="clearAll"
        >
          <Trash2 />
        </Button>
      </div>
    </div>
  </div>

  <DataNotice v-if="showNotice" @dismiss="dismissNotice" />
</template>
