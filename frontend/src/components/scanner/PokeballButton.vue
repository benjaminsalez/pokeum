<script setup lang="ts">
withDefaults(
  defineProps<{
    disabled?: boolean;
    scanning?: boolean;
  }>(),
  {
    disabled: false,
    scanning: false,
  },
);

defineEmits<{
  click: [];
}>();
</script>

<template>
  <button
    type="button"
    class="group relative grid h-[5.5rem] w-[5.5rem] shrink-0 touch-manipulation place-items-center rounded-full border-0 bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-4 focus-visible:ring-offset-transparent disabled:cursor-not-allowed [@media(max-height:700px)]:h-[4.9rem] [@media(max-height:700px)]:w-[4.9rem]"
    :disabled="disabled"
    :aria-label="scanning ? 'Recognizing card' : 'Scan card'"
    :aria-busy="scanning"
    @click="$emit('click')"
  >
    <span
      aria-hidden="true"
      class="relative z-10 block h-[4.75rem] w-[4.75rem] overflow-hidden rounded-full border-[3px] border-zinc-900/95 bg-[linear-gradient(to_bottom,#ef4444_0%,#dc2626_43%,#27272a_43%,#18181b_56%,#ffffff_56%,#e4e4e7_100%)] shadow-[0_12px_28px_rgba(0,0,0,0.36),inset_0_1px_1px_rgba(255,255,255,0.42)] transition duration-150 ease-out group-hover:brightness-105 group-hover:shadow-[0_15px_34px_rgba(0,0,0,0.42),0_0_0_5px_rgba(255,255,255,0.09),inset_0_1px_1px_rgba(255,255,255,0.42)] group-active:scale-95 motion-reduce:transition-none motion-reduce:group-active:scale-100 [@media(max-height:700px)]:h-[4.3rem] [@media(max-height:700px)]:w-[4.3rem]"
      :class="disabled && !scanning ? 'opacity-50 grayscale' : ''"
    >
      <span
        class="absolute left-[0.9rem] top-[0.58rem] h-[0.52rem] w-5 -rotate-[24deg] rounded-full bg-white/45 blur-[0.5px]"
      />
      <span
        class="absolute left-1/2 top-1/2 grid h-7 w-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-[3px] border-zinc-900 bg-zinc-50 shadow-[0_2px_5px_rgba(0,0,0,0.28),inset_0_0_0_1px_rgba(255,255,255,0.8)]"
      >
        <span class="h-3 w-3 rounded-full border border-zinc-400 bg-white" />
      </span>
    </span>
  </button>
</template>
