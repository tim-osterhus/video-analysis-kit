# Prepare and inspect video evidence

After [setup](setup.md), use the CLI or the installed skill launcher. The following commands assume you are in the checkout and have a configured environment. Put a custom global `--config PATH` before the subcommand when needed.

```bash
.venv/bin/video-analysis prepare '/absolute/path/to/demo.mp4' \
  --profile general --detail balanced \
  --start 00:30 --end 02:00 --max-frames 12
```

Replace the local path with a supported public HTTPS video URL to prepare remote media. A time range narrows the requested evidence; it does not guarantee the site downloader can avoid downloading the full video. The source duration limit applies to the source, not just the selected range.

## Preparation controls

| Option | Meaning |
| --- | --- |
| `--profile general` | Direct summary or question answering |
| `--profile stack-relevance` | Compare demonstrated capabilities with a known stack |
| `--profile competitor-creative` | Inspect a video's hook, structure, claims, and call to action |
| `--profile implementation-handoff` | Extract demonstrated behavior and open requirements |
| `--detail transcript\|efficient\|balanced` | Select the preparation detail level |
| `--start TIME --end TIME` | Request a time range, in seconds or colon-separated time |
| `--timestamps TIMES` | Comma-separated absolute frame timestamps, such as `00:30,01:15` |
| `--max-frames 1..100` | Bound sampled frames |
| `--resolution 512\|1024` | Select frame resolution |
| `--transcription local\|captions-only` | Allow the configured worker's speech model, or use captions only |
| `--max-duration SECONDS` | Lower the maximum source duration; maximum 7,200 seconds |
| `--timeout SECONDS` | Bound preparation time, from 30 to 3,600 seconds |

`--transcription local` means transcription on the configured processing host, including an SSH worker. It does not change the execution host. Captions may already supply the transcript. Profiles guide the later agent analysis; preparation itself does not write a fact-checked answer.

Success prints JSON with `local_run_dir`, `analysis_packet`, `manifest`, and `status`; SSH runs also include `remote_run_dir`. Use the returned paths rather than constructing a run directory name.

Read `analysis-packet.md`, then the manifest's provenance, transcript source, timestamps, and limitations. Inspect sampled frames with an image-reading tool. Keep presenter claims distinct from observed evidence, and use external sources when the user's question requires verification. A transcript-only bundle cannot support visual claims.

```bash
.venv/bin/video-analysis validate '/absolute/path/from/result/manifest.json'
```

Validation checks the local manifest and evidence files. With SSH execution the retrieved bundle describes the evidence retained locally; the worker keeps the larger media files. Reuse the bundle for follow-up questions when it contains the needed evidence.

## Recovery

Run `doctor` after a runtime or connectivity failure. If worker preparation succeeded but retrieval failed, use the returned remote run directory:

```bash
.venv/bin/video-analysis fetch '/absolute/remote/run/directory'
```

Do not change execution hosts or install a different speech runtime just to hide a failure. Report unavailable captions, failed transcription, blocked site access, incomplete retrieval, or insufficient visual coverage as the specific limitation. Website extractor behavior can change independently of this repository.

## Retention and cleanup

Successful runs persist until explicitly removed. On an SSH setup, local evidence and remote processing artifacts occupy different machines. Manifests and transcripts can contain source paths, URLs, spoken private information, and other sensitive content. Review a bundle before sharing it.

Cleanup deletes managed run directories older than the requested age on the configured execution host:

```bash
.venv/bin/video-analysis cleanup --older-than-days 7
```

This is a deletion command, not a preview. For SSH execution it targets worker runs; local fetched evidence is separate. To clean local retained evidence, use a local configuration pointing to that data directory, or explicitly remove the inspected local run directories. Cleanup does not uninstall a model, remove the skill, or publish anything. Request cleanup only when you intend to discard those runs.
