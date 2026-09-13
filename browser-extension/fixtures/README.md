# Browser interaction fixture

The packaged `src/cu/playground.html` provides the shared browser fixture:
ordinary fields, synthetic sensitive labels, delayed readiness, replacement
and covered targets, application/event counters, hover reveal, collection
selection with a simulated partial failure, scrolling and generated media.

Start it explicitly with `cu playground`. The command prints a loopback URL;
open that URL in the selected browser. Ctrl-C stops the foreground server.
No extension is needed for ordinary native CU interaction. The fixture is
packaged with the CLI and also works outside a repository checkout.

For extension acceptance, explicitly grant the disposable tab the required
read or interaction capability. Exercise stale refs, sensitive-field refusal,
inactive tabs, Stop and navigation/reload revocation. Stop before changing grant
mode. Native motion, synthetic hover and rendered CSS hover remain distinct;
use event counters and observed controls to tell them apart.

The collection action reports synthetic completion/failure counters; it does
not download four real files. Generated media is a local canvas stream with no
finite file duration. Use an explicit finite-media fixture for duration tests;
page labels and stream metadata are not verified file duration.

The task panel supports a native basics trial and a follow-up. Reset reloads the
page, creates a new trial ID and clears its state. The optional receipt download
contains synthetic fixture state and page-relative elapsed time, not controller
tokens, billing or quota. Close the disposable tab and stop the server when the
explicit trial is finished.
