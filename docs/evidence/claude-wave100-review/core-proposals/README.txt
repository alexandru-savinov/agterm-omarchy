Three portable core parser proposals from wave #92

All patches target source 8458e16d06b2432cd6895955b6be972b1edae93d.
These are inert evidence artifacts, not active source edits.
Swift compilation and test execution remain UNVERIFIED: no Swift compiler is available in this environment.
Patch applicability was independently checked by the coordinator.
The Python demonstrations are string-only translations, not execution of Swift or shell commands.

custom-command.patch
CommandContext.expand matches {AGT_X} inside ${AGT_X}, replacing shell environment references with raw text before /bin/sh -c.
An earlier literal opening brace can also consume the closing brace of a later real token.
The proposal preserves simple shell parameter references and continues scanning past literal opening braces.
Apply the patch and copy CustomCommandExpansionRegressionTests.swift.txt into agtermCore/Tests/agtermCoreTests/CustomCommandExpansionRegressionTests.swift.
Run the full host-free suite in the supported Swift toolchain, including existing CustomCommand tests.
Nested shell expressions such as ${OTHER:-{AGT_SELECTION}} still expand their inner bare token; arbitrary shell syntax is not opaque.
The fix preserves the documented raw substitution behavior of bare {AGT_X}; it is not a shell parser or escaping change for raw tokens.
The string-only demonstration uses benign quoted text and does not execute a shell payload.

quoted-hooks.patch
AgentHooksInstall.entryUsesWrapper compares raw paths against the shell-quoted commands it generated.
An apostrophe in scriptDir breaks that substring match and each reinstall appends another hook for each of four events.
The proposal recognizes both raw and generated quoted paths; exact ownership/migration rules stay unchanged.
The patch includes proposed Swift regressions for repeated installation, legacy migration and customized-hook preservation.
The Python demonstration proves only the string-membership mismatch and JSON round trip.

denylist-crlf.patch
CommandRestore.parseDenylist splits on the LF Character; CRLF is one distinct Swift Character.
A CRLF comment-first file is treated as a single comment; a noncomment file can combine basenames, defeating exact denylist matching.
The proposal splits on Character.isNewline and includes LF, CRLF, CR and mixed-line-ending restore-decision regressions.
Both Linux and macOS use this shared parser.
Swift language reference: https://developer.apple.com/documentation/swift/character/isnewline

Replay from a clean checkout at the source revision
1. Apply one proposal with git apply --check followed by git apply.
2. For custom-command only, copy its separate test source as described above.
3. Run swift test from agtermCore and the repository lint/build checks appropriate to adopted changes.
4. Existing owners review compatibility and adoption; these artifacts do not authorize release or claim runtime acceptance.
