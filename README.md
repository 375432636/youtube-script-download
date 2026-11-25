# YouTube 字幕归档与视频脚本 Agent

这是一个可在 Codex 仓库中使用的 Agent，由三个独立 Skill 组成。归档可以从任何公开 YouTube 视频或频道开始，风格指南按来源单独维护。差评君 `@xpinman` 是首个归档任务。

| 在 Codex 中调用 | 用法 |
| --- | --- |
| `$youtube-subtitle-archive` | 「归档这个频道 / 视频的字幕到 Google Drive」 |
| `$video-style-analysis` | 「从这个频道的归档字幕整理一份脚本结构和语言风格指南」 |
| `$video-script-writing` | 「参考这份风格指南，写一篇关于这个新选题的中文口播稿」 |

在仓库根目录打开 Codex。字幕归档需要已连接的 Google Drive，以及 Python 3.11+、Node.js 和 FFmpeg。仓库中的 Python 工具负责字幕、限速和清单；Drive 写入由 Skill 调用当前账号的连接器。

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
youtube-subtitles inventory @xpinman
youtube-subtitles run @xpinman --limit 2
youtube-subtitles queue @xpinman --limit 20
youtube-subtitles status @xpinman
```

也可以把 `@xpinman` 替换为视频链接，如 `https://www.youtube.com/watch?v=VIDEO_ID`。在同一工作区以相同来源重新运行 `run` 即可续跑；`--retry-failed` 会把失败视频纳入同一个共享限速。遇到 YouTube 429 时，进程返回 75 并记录暂停截止时间。

- [架构与数据流](docs/architecture.md)
- [决策记录](docs/decisions.md)
- [讨论记录](docs/discussion-log.md)

首批项目资料见 [归档清单](catalog/xpinman.csv) 和 [差评君口播风格指南](styles/xpinman.md)。影视飓风的编导、评测与纪实写法见 [影视飓风视频脚本风格指南](styles/mediastorm6801.md)。清单状态随批次核对更新。

本地回归检查可用 `python -m unittest discover -s tests`。这些检查无需连接 YouTube 或 Google Drive。

[频道归档进度](docs/archive-progress.md)记录本轮停止点和续跑方式。使用 `youtube-subtitles run SOURCE --track-target 210` 可在该来源累计取得约 210 份校验过的本地 SRT 后暂停。最后一个视频的完整字幕轨会保留。
