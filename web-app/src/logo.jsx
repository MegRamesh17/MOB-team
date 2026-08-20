import React from "react";

/**
 * Ascend wordmark.
 *
 * Two curved leaf blades (dark forest -> fresh green gradient, left to right) with a
 * small sage bud above -- concept #8 from the brand board, hand-traced from the
 * reference image rather than a real vector import (see the note at the bottom of this
 * file). No enclosing badge/box: the brief is explicit that the mark must sit directly
 * on the near-black sidebar with nothing behind it, and the gradient reads fine on both
 * that and the cream page background, so `light` only needs to switch the wordmark text.
 */
const VIEWBOX_W = 340;
const VIEWBOX_H = 150;

export function Logo({ size = 28, light = false }) {
  return (
    <svg height={size} width={size * (VIEWBOX_W / VIEWBOX_H)} viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`} fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="ascend-leaf-l" x1="0.1" y1="1" x2="0.9" y2="0.1">
          <stop offset="0%" stopColor="#0D4A30" />
          <stop offset="100%" stopColor="#2F9E5C" />
        </linearGradient>
        <linearGradient id="ascend-leaf-r" x1="0.1" y1="1" x2="0.9" y2="0.1">
          <stop offset="0%" stopColor="#3FA85E" />
          <stop offset="100%" stopColor="#A6DE93" />
        </linearGradient>
      </defs>
      <path d="M54,140 C22,126 -1,90 9,50 C14,32 28,20 42,18 C33,26 27,44 30,64 C34,90 43,116 54,140 Z" fill="url(#ascend-leaf-l)" />
      <path d="M28,66 C31,88 41,114 54,140" stroke="rgba(255,255,255,0.32)" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M60,140 C90,124 111,86 100,47 C95,30 82,19 68,18 C76,27 81,45 78,64 C74,90 66,116 60,140 Z" fill="url(#ascend-leaf-r)" />
      <path d="M79,66 C76,88 66,114 60,140" stroke="rgba(255,255,255,0.32)" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="77" cy="17" r="12" fill="#9AD4C2" />
      <text
        x="128" y="100"
        fontFamily="'Playfair Display', Georgia, serif" fontSize="62" fontWeight="700"
        fill={light ? "#F0EAD8" : "#0F1214"} letterSpacing="-1"
      >
        ascend
      </text>
    </svg>
  );
}

/**
 * This is a stand-in, hand-drawn to match the reference closely rather than pixel-
 * identical to it -- the brief names `ascend_logo_concept_8.svg` /
 * `ascend_logo_concept_8_transparent.png` as the source files, but neither ever
 * reached this repo (only the brief markdown and one inline chat image did). Drop the
 * real file at web-app/public/logo.svg (or .png) once you have it, and replace the
 * <svg> above with:
 *
 *     return <img src="/logo.svg" alt="Ascend" style={{ height: size }} />;
 *
 * An SVG is preferable to a PNG here -- it scales cleanly at every sidebar/favicon size
 * this component is used at, where a raster source needs multiple exports.
 */
