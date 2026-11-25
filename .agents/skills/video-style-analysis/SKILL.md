---
name: video-style-analysis
description: Derive a reusable video script style guide from archived YouTube subtitles. Use to internalize a channel's script logic, spoken language, pacing, transitions, and recurring writing patterns from timestamped samples.
---

# Analyze video script style

1. Identify the source group and locate its public catalog or the corresponding Drive archive. Use original subtitles, preferring human-authored tracks where available. Read the subtitle files from the local cache or use the connected Google Drive raw-file fetch.
2. Sample different points in the channel's chronology and analyze long videos and Shorts separately. If a category has too few complete transcripts, state the actual sample size. Keep the video ID, timestamp range, language, and track type for each piece of evidence. Subtitle wording can support observations about delivery; infer visuals only from explicit user-provided visual evidence.
3. Derive the functional structure of each sample: opening promise or tension, context, progression of claims and examples, transitions, caveats, and payoff. Compare sentence length, spoken register, explanation habits, rhythm, and calls to action. Distinguish recurring techniques, situational variants, and outliers. Review a few samples outside the initial set when available to check whether the rules transfer.
4. Save a concise guide at `styles/<source>.md` with separate long-video and Shorts guidance where evidence permits. Include actionable drafting cues, a small sample table, timestamped evidence for the main observations, limitations, and what to avoid. Use links and paraphrases instead of reproducing subtitle passages. Keep each source in its own guide unless the user requests a comparison.
5. Return the guide and the actual sampling coverage. Pass the guide to `$video-script-writing` when a script is requested.
