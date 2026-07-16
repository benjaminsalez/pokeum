<script setup lang="ts">
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";

defineEmits<{ dismiss: [] }>();
</script>

<template>
  <div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/55 p-4">
    <Card role="dialog" aria-modal="true" aria-labelledby="notice-title" class="w-full max-w-md p-5">
      <h2 id="notice-title" class="text-sm font-bold">Heads up</h2>
      <p class="mt-2 text-sm leading-relaxed text-zinc-600">
        When you save a scanned card, the photo you captured and its match are collected to
        help improve recognition. Photos may include what's around the card.
      </p>
      <div class="pt-4">
        <Button size="lg" class="min-h-12 w-full" @click="$emit('dismiss')">Got it</Button>
      </div>
    </Card>
  </div>
</template>
