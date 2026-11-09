---
applyTo: "**"
---

At the start of each new conversation, before answering the user, send a visible message: "Loaded (filepath)/AGENTS.md".

Read the LocalAGENTS.md file if present. There will usually be one at the top
level project directory. 

When using temporary files and directory in an application, have the application
create a temporary directory. Then put the working temporary files and
directories under it. Then when the application needs to clean up temp files
it can remove that top level directory recursively in case the application
lost track of any of the other files.

When working with vscode use a tmp directory at the open directory location. For example when
the open folder/directory is ~/work/thumbnail/ use ~/work/thumbnail/tmp/ as the temporary directory.
Create it if needed.

Before consulting external reference implementations, check the applicable standards
and design documents in `docs/` first. Treat those local documents as the primary source and use
reference code only to cross-check details that remain unclear.

Making progress with difficult tasks.
When it is requested to implement an item, but the item is too complex, break the item
into several smaller tasks. Don't have one of the tasks, the last one for example, be
a "and everything else" task. The smaller tasks should each be achievable. Do not
stop once the smaller tasks are determined.  Once the
smaller task list is created, implement the first of the smaller tasks.  If that item
is too complex to implement, recurse this rule.
When an item can not be implemented, adding a test for that item that shows that it is
unimplemented does not count as progress. Adding a test is ok, but it is required to
implement the dependent item that was causing it to report that it was unimplemented.
If that dependent item is too complex, resurse this rule.

There is a python venv in ~/work/.venv for use by all projects below ~/work directory.

Don't make up new work while waiting for a test to run. Just wait until the test completes. Don't poll for process completion or any other action that
uses tokens. Don't do anything that would not have been done if the
test ran faster to completion.

Run all tests with time limits using a tool like timeout. Use a default time
limit of 60 seconds.  If a longer time is required, confirm with the user.

When possible, use Haiku error messages as long as they don't interfere with
giving clear error information. Otherwise use a regular error message.

Do not produce code with memory leaks.

When working with git do not stage or commit soft links unless they are requested by name.

Do not stage or check files larger than 1 MiB into git without confirmation. Do not
use the production git repository for tests.  If a repository is needed for tests,
create a new temporary repository after confirmation.
