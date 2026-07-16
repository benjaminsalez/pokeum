<script setup lang="ts">
import { ref } from "vue";

withDefaults(
  defineProps<{
    scanning?: boolean;
  }>(),
  {
    scanning: false,
  },
);

const root = ref<HTMLDivElement | null>(null);

/** On-screen rect of the guide frame, used to crop the capture to the card. */
function getRect(): DOMRect | null {
  return root.value?.getBoundingClientRect() ?? null;
}

defineExpose({ getRect });
</script>

<template>
  <div
    ref="root"
    aria-hidden="true"
    class="w-[min(72vw,21rem)] [@media(max-height:760px)]:w-[min(58vw,16.5rem)] md:w-[min(40vh,22rem)]"
  >
    <div
      class="relative aspect-[63/88] w-full overflow-hidden rounded-[1.25rem] border border-white/15 bg-gradient-to-b from-white/[0.03] to-transparent shadow-[0_0_0_9999px_rgba(0,0,0,0.34),inset_0_0_0_1px_rgba(0,0,0,0.18),0_18px_60px_rgba(0,0,0,0.22)]"
    >
      <span
        class="absolute -left-px -top-px z-10 h-9 w-9 rounded-tl-[1.25rem] border-l-[3px] border-t-[3px] border-white/90 drop-shadow-[0_2px_6px_rgba(0,0,0,0.42)]"
      />
      <span
        class="absolute -right-px -top-px z-10 h-9 w-9 rounded-tr-[1.25rem] border-r-[3px] border-t-[3px] border-white/90 drop-shadow-[0_2px_6px_rgba(0,0,0,0.42)]"
      />
      <span
        class="absolute -bottom-px -left-px z-10 h-9 w-9 rounded-bl-[1.25rem] border-b-[3px] border-l-[3px] border-white/90 drop-shadow-[0_2px_6px_rgba(0,0,0,0.42)]"
      />
      <span
        class="absolute -bottom-px -right-px z-10 h-9 w-9 rounded-br-[1.25rem] border-b-[3px] border-r-[3px] border-white/90 drop-shadow-[0_2px_6px_rgba(0,0,0,0.42)]"
      />

      <div
        class="absolute left-2 right-2 top-[4%] z-[3] h-px bg-sky-300 opacity-0 shadow-[0_0_6px_1px_rgba(125,211,252,0.65)]"
        :class="
          scanning
            ? 'animate-scanner-sweep opacity-100 motion-reduce:top-1/2 motion-reduce:animate-none'
            : ''
        "
      />
    </div>
  </div>
</template>
