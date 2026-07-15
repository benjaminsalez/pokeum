import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind class lists the shadcn way. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
