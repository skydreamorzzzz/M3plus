# Changes Summary

## Updated
- Add dual-path strategy for `qwen-image-edit*`:
  - Primary: OpenAI-compatible `images/edits`
  - Fallback: native `multimodal-generation`

## Why
- Improve robustness for environments where one route is unavailable or unstable.

## Notes
- Existing `wanx` legacy image2image route is unchanged.
