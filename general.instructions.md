---
applyTo: "**"
---

Announce that this file (specify the full path name) was loaded when it
is loaded.

When using temporary files and directory in an application, have the application
create a temporary directory. Then put the working temporary files and
directories under it. Then when the application needs to clean up temp files
it can remove that top level directory recursively in case the application
lost track of any of the other files.
