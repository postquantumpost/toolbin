---
applyTo: "**"
---

At the start of each new conversation, before answering the user, send a visible message: "Loaded /home/jph/work/videye/AGENTS.md".

When using temporary files and directory in an application, have the application
create a temporary directory. Then put the working temporary files and
directories under it. Then when the application needs to clean up temp files
it can remove that top level directory recursively in case the application
lost track of any of the other files.

When working with vscode use a tmp directory at the open directory location. For example when
the open folder/directory is ~/work/thumbnail/ use ~/work/thumbnail/tmp/ as the temporary directory.
Create it if needed.

Before consulting external reference implementations such as FFmpeg, check the applicable standards
and design documents in `docs/` first. Treat those local documents as the primary source and use
reference code only to cross-check details that remain unclear.

When working with git do not stage or commit soft links unless they are requested by name.
