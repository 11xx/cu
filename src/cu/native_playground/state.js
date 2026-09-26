(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CUNativePlaygroundState = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  const SCHEMA = "cu-native-playground/1";
  const DEFAULT_TIMEOUT_MS = 5000;
  const DEFAULT_SUITE_SEED = "native-playground-seed-1";
  let generatedId = 0;

  const CAPABILITIES = Object.freeze({
    native_input: Object.freeze({
      state: "available",
      reason: "ordinary browser input is in scope",
    }),
    extension_grant: Object.freeze({
      state: "unavailable",
      reason: "native tasks do not require an extension grant",
    }),
    real_file_output: Object.freeze({
      state: "unavailable",
      reason: "the native suite scores visible synthetic outcomes only",
    }),
  });

  function copy(value) {
    if (value === undefined) return undefined;
    if (Array.isArray(value)) return value.map(copy);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, copy(item)]));
    }
    return value;
  }

  function freeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.values(value).forEach(freeze);
    return Object.freeze(value);
  }

  function id(prefix) {
    generatedId += 1;
    return `${prefix}-${generatedId}`;
  }

  function hash(value) {
    let result = 2166136261;
    for (const character of String(value)) {
      result ^= character.charCodeAt(0);
      result = Math.imul(result, 16777619);
    }
    return result >>> 0;
  }

  function fixtureFor(definition, suiteSeed) {
    if (!definition.variants) return null;
    const variantIndex = hash(`${suiteSeed}:${definition.task_id}`) % definition.variants.length;
    return {
      ...copy(definition.variants[variantIndex]),
      variant_index: variantIndex,
      fixture_seed: `${definition.seed}/${suiteSeed}/variant-${variantIndex + 1}`,
    };
  }

  function described(value, fixture) {
    return typeof value === "function" ? value(fixture) : value;
  }

  function sorted(values) {
    return [...values].map(String).sort();
  }

  function sameMembers(left, right) {
    return sorted(left).join("\u0000") === sorted(right).join("\u0000");
  }

  function sameSequence(left, right) {
    return left.length === right.length && left.every((value, index) => value === right[index]);
  }

  function sameRecords(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function unique(values) {
    return [...new Set(values.map(String))];
  }

  function percentage(value) {
    return Math.max(0, Math.min(100, Math.round(value * 100)));
  }

  function numberOr(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function basicsDefinition() {
    return {
      task_id: "native-basics",
      task_version: 1,
      seed: "native-basics-seed-1",
      title: "Native basics",
      objective: "Replace both ordinary fields and press Apply once with the exact values.",
      scope: "The ordinary value and notes fields, plus the Apply button.",
      permitted_actions: "Edit the two fields and press Apply once.",
      forbidden_effects: "A wrong or repeated Apply is a failed committed answer.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: () => ({ entry: "8080", notes: "Original notes", applications: 0 }),
      evaluate(action, taskState) {
        if (action !== "apply") return null;
        const evidence = {
          entry: taskState.entry,
          notes: taskState.notes,
          applications: taskState.applications,
        };
        if (taskState.entry !== "4242" || taskState.notes !== "Luna was here") {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-submission",
              message: "Apply committed values that do not match the objective.",
            },
            evidence,
          };
        }
        if (taskState.applications !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "repeated-submission",
              message: "The objective permits exactly one Apply action.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: { code: "objective-reached", message: "The exact values were applied once." },
          evidence,
        };
      },
    };
  }

  function followupDefinition() {
    return {
      task_id: "native-followup",
      task_version: 1,
      seed: "native-followup-seed-1",
      title: "Readiness and scroll",
      objective: "Schedule readiness, wait for Ready, then press Bottom target once.",
      scope: "The readiness control, Ready signal and scroll-pane Bottom target.",
      permitted_actions:
        "Schedule readiness, wait for the visible Ready signal, scroll and press Bottom target once.",
      forbidden_effects:
        "Pressing Bottom target before Ready or more than once is a failed committed answer.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: () => ({ ready: false, readiness_requests: 0, bottom: 0 }),
      evaluate(action, taskState) {
        if (action !== "bottom") return null;
        const evidence = {
          ready: taskState.ready,
          readiness_requests: taskState.readiness_requests,
          bottom: taskState.bottom,
        };
        if (!taskState.ready) {
          return {
            outcome: "FAIL",
            reason: {
              code: "premature-bottom",
              message: "Bottom target was committed before the Ready signal.",
            },
            evidence,
          };
        }
        if (taskState.bottom !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "repeated-bottom",
              message: "The objective permits exactly one Bottom target action.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "Ready preceded one Bottom target action.",
          },
          evidence,
        };
      },
    };
  }

  function dispatchDefinition() {
    const variants = [
      {
        destination: "North dock",
        quantity: 4,
        notes: "Leave the crate by the blue gate.",
        default_destination: "South depot",
        default_quantity: 1,
        default_notes: "Draft dispatch",
      },
      {
        destination: "Archive annex",
        quantity: 7,
        notes: "Ring the service bell twice.",
        default_destination: "West dock",
        default_quantity: 2,
        default_notes: "Draft dispatch",
      },
      {
        destination: "South loading bay",
        quantity: 2,
        notes: "Use the covered pallet lane.",
        default_destination: "North dock",
        default_quantity: 5,
        default_notes: "Draft dispatch",
      },
    ];
    return {
      task_id: "dispatch-desk",
      task_version: 1,
      seed: "dispatch-desk-seed-1",
      title: "Dispatch desk",
      variants,
      objective: ({ fixture }) =>
        `Send ${fixture.quantity} units to ${fixture.destination} with notes “${fixture.notes}”, then submit once.`,
      scope: "The destination, quantity, notes and one dispatch receipt.",
      permitted_actions: "Edit the visible dispatch fields and submit the dispatch once.",
      forbidden_effects:
        "Typing is not submission; a wrong or duplicate dispatch fails the attempt.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        brief: {
          destination: fixture.destination,
          quantity: String(fixture.quantity),
          notes: fixture.notes,
        },
        destination: fixture.default_destination,
        quantity: String(fixture.default_quantity),
        notes: fixture.default_notes,
        dispatch_submissions: 0,
        dispatch_pending: false,
        dispatch_receipt: null,
      }),
      evaluate(action, taskState) {
        if (!["dispatch-submit", "dispatch-receipt"].includes(action)) return null;
        const evidence = {
          submitted: {
            destination: taskState.destination,
            quantity: taskState.quantity,
            notes: taskState.notes,
          },
          expected: taskState.brief,
          dispatch_submissions: taskState.dispatch_submissions,
          dispatch_pending: taskState.dispatch_pending,
          dispatch_receipt: taskState.dispatch_receipt,
        };
        if (taskState.dispatch_submissions > 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-dispatch",
              message: "The dispatch was submitted more than once; retry and submit one time only.",
            },
            evidence,
          };
        }
        if (taskState.destination !== taskState.brief.destination) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-destination",
              message: "Choose the destination named in the dispatch brief before submitting.",
            },
            evidence,
          };
        }
        if (String(taskState.quantity) !== taskState.brief.quantity) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-quantity",
              message: "Set quantity to the exact number named in the dispatch brief.",
            },
            evidence,
          };
        }
        if (taskState.notes !== taskState.brief.notes) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-notes",
              message: "Use the exact notes from the dispatch brief before submitting.",
            },
            evidence,
          };
        }
        if (action === "dispatch-submit") return null;
        if (taskState.dispatch_receipt === null || taskState.dispatch_submissions !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "missing-dispatch-receipt",
              message: "Submit the dispatch once and wait for its visible receipt.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "The exact dispatch produced one receipt.",
          },
          evidence,
        };
      },
    };
  }

  function parcelDefinition() {
    const variants = [
      { job_id: "parcel-job-amber", job_label: "Amber invoice sync", ready_delay_ms: 180 },
      { job_id: "parcel-job-cobalt", job_label: "Cobalt manifest build", ready_delay_ms: 290 },
      { job_id: "parcel-job-lime", job_label: "Lime receipt pack", ready_delay_ms: 220 },
    ];
    return {
      task_id: "delayed-parcel",
      task_version: 1,
      seed: "delayed-parcel-seed-1",
      title: "Delayed parcel",
      variants,
      objective: ({ fixture }) =>
        `Start one ${fixture.job_label} job, wait until Ready, then acknowledge receipt ${fixture.job_id}.`,
      scope: "One visible job, its Ready state and the matching receipt acknowledgement.",
      permitted_actions:
        "Start the named job once, wait for Ready, then acknowledge its matching receipt.",
      forbidden_effects:
        "Do not acknowledge early, acknowledge another job, or start the job twice.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        job_label: fixture.job_label,
        expected_job_id: fixture.job_id,
        job_id: null,
        job_starts: 0,
        ready: false,
        ready_job_id: null,
        acknowledgement_input: fixture.job_id,
        acknowledgement_count: 0,
        acknowledged_job_ids: [],
        parcel_receipt: null,
      }),
      evaluate(action, taskState, event) {
        if (!["parcel-start-job", "parcel-ack"].includes(action)) return null;
        const evidence = {
          expected_job_id: taskState.expected_job_id,
          job_id: taskState.job_id,
          job_starts: taskState.job_starts,
          ready: taskState.ready,
          ready_job_id: taskState.ready_job_id,
          acknowledged_job_id: event.job_id ?? null,
          acknowledgement_count: taskState.acknowledgement_count,
          acknowledged_job_ids: taskState.acknowledged_job_ids,
          parcel_receipt: taskState.parcel_receipt,
        };
        if (action === "parcel-start-job") {
          if (taskState.job_starts > 1) {
            return {
              outcome: "FAIL",
              reason: {
                code: "duplicate-job-start",
                message: "Start the named parcel job once; retry before starting it again.",
              },
              evidence,
            };
          }
          return null;
        }
        if (taskState.job_starts === 0) {
          return {
            outcome: "FAIL",
            reason: {
              code: "job-not-started",
              message: "Start the named parcel job before acknowledging a receipt.",
            },
            evidence,
          };
        }
        if (String(event.job_id ?? "") !== taskState.expected_job_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-job",
              message: "Acknowledge the receipt whose job ID matches the started job.",
            },
            evidence,
          };
        }
        if (!taskState.ready || taskState.ready_job_id !== taskState.expected_job_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "parcel-not-ready",
              message: "Wait for the visible Ready state for this job before acknowledging it.",
            },
            evidence,
          };
        }
        if (taskState.acknowledgement_count > 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-acknowledgement",
              message: "Acknowledge the matching parcel receipt once only.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "Ready preceded one acknowledgement of the matching job.",
          },
          evidence,
        };
      },
    };
  }

  function archiveDefinition() {
    const variants = [
      {
        pane_id: "archive-inner-amber",
        target_id: "archive-bottom-amber",
        inner_depth: 760,
        inner_viewport: 170,
        initial_inner_scroll: 40,
        initial_outer_scroll: 90,
        outer_content_height: 1050,
      },
      {
        pane_id: "archive-inner-cobalt",
        target_id: "archive-bottom-cobalt",
        inner_depth: 1180,
        inner_viewport: 190,
        initial_inner_scroll: 180,
        initial_outer_scroll: 260,
        outer_content_height: 1320,
      },
      {
        pane_id: "archive-inner-lime",
        target_id: "archive-bottom-lime",
        inner_depth: 1540,
        inner_viewport: 210,
        initial_inner_scroll: 260,
        initial_outer_scroll: 140,
        outer_content_height: 1480,
      },
    ];
    return {
      task_id: "archive-elevator",
      task_version: 1,
      seed: "archive-elevator-seed-1",
      title: "Archive elevator",
      variants,
      objective: ({ fixture }) =>
        `In INNER pane ${fixture.pane_id}, reach the named Bottom target ${fixture.target_id} and click it once.`,
      scope: "An independently scrollable INNER archive pane inside a nearby outer scroll area.",
      permitted_actions:
        "Scroll the outer context and INNER pane, observe target progress, then click Bottom once.",
      forbidden_effects:
        "Outer scrolling or a numeric offset alone is not target evidence; a wrong pane or target fails.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        pane_id: fixture.pane_id,
        target_id: fixture.target_id,
        inner_depth: fixture.inner_depth,
        inner_viewport: fixture.inner_viewport,
        outer_scroll: fixture.initial_outer_scroll,
        inner_scroll: fixture.initial_inner_scroll,
        inner_progress: percentage(
          (fixture.initial_inner_scroll + fixture.inner_viewport) /
            (fixture.inner_depth + fixture.inner_viewport),
        ),
        target_reached: false,
        target_receipt: null,
        bottom_clicks: 0,
      }),
      evaluate(action, taskState, event) {
        if (action !== "elevator-bottom") return null;
        const evidence = {
          pane_id: taskState.pane_id,
          target_id: taskState.target_id,
          clicked_pane_id: event.pane_id ?? null,
          clicked_target_id: event.target_id ?? null,
          outer_scroll: taskState.outer_scroll,
          inner_scroll: taskState.inner_scroll,
          inner_progress: taskState.inner_progress,
          target_reached: taskState.target_reached,
          target_receipt: taskState.target_receipt,
          bottom_clicks: taskState.bottom_clicks,
        };
        if (event.pane_id !== taskState.pane_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-pane",
              message:
                "Click the Bottom target inside the named INNER pane, not a nearby scroll area.",
            },
            evidence,
          };
        }
        if (event.target_id !== taskState.target_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-target",
              message: "Use the Bottom target whose identity is shown in the selected INNER pane.",
            },
            evidence,
          };
        }
        if (!taskState.target_reached || taskState.target_receipt?.reached !== true) {
          return {
            outcome: "FAIL",
            reason: {
              code: "target-not-reached",
              message:
                "Scroll the INNER pane until its target receipt says reached, then click once.",
            },
            evidence,
          };
        }
        if (taskState.bottom_clicks !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "repeated-bottom",
              message: "The archive target may be clicked exactly once per attempt.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "The observed INNER target produced one Bottom receipt.",
          },
          evidence,
        };
      },
    };
  }

  function galleryDefinition() {
    const variants = [
      {
        group_action: "Archive selected",
        required_ids: ["gallery-amber", "gallery-cobalt"],
        failed_id: "gallery-cobalt",
        items: [
          { item_id: "gallery-amber", title: "Amber map", detail: "survey" },
          { item_id: "gallery-cobalt", title: "Cobalt ledger", detail: "finance" },
          { item_id: "gallery-lime", title: "Lime poster", detail: "event" },
          { item_id: "gallery-rose", title: "Rose ticket", detail: "travel" },
        ],
      },
      {
        group_action: "Tag selected",
        required_ids: ["gallery-lime", "gallery-rose", "gallery-slate"],
        failed_id: "gallery-rose",
        items: [
          { item_id: "gallery-lime", title: "Lime receipt", detail: "billing" },
          { item_id: "gallery-rose", title: "Rose parcel", detail: "delivery" },
          { item_id: "gallery-slate", title: "Slate label", detail: "inventory" },
          { item_id: "gallery-amber", title: "Amber note", detail: "memo" },
          { item_id: "gallery-cobalt", title: "Cobalt card", detail: "contact" },
        ],
      },
      {
        group_action: "Review selected",
        required_ids: ["gallery-cobalt", "gallery-slate"],
        failed_id: "gallery-cobalt",
        items: [
          { item_id: "gallery-cobalt", title: "Cobalt print", detail: "print" },
          { item_id: "gallery-slate", title: "Slate proof", detail: "review" },
          { item_id: "gallery-lime", title: "Lime draft", detail: "draft" },
          { item_id: "gallery-rose", title: "Rose sample", detail: "sample" },
        ],
      },
    ];
    function itemTitle(fixture, itemId) {
      return fixture.items.find((item) => item.item_id === itemId)?.title || itemId;
    }
    return {
      task_id: "gallery-crew",
      task_version: 1,
      seed: "gallery-crew-seed-1",
      title: "Gallery crew",
      variants,
      objective: ({ fixture }) =>
        `Hover a representative card, select exactly ${fixture.required_ids
          .map((itemId) => itemTitle(fixture, itemId))
          .join(
            ", ",
          )}, run “${fixture.group_action}” once, then recover only ${itemTitle(fixture, fixture.failed_id)}.`,
      scope:
        "Gallery cards with native CSS hover controls, exact membership, one group action and one recovery.",
      permitted_actions:
        "Hover a card, select the named subset, use the group action once, then recover the failed item.",
      forbidden_effects:
        "Do not group incomplete or duplicate membership, or reapply items that already succeeded.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        items: fixture.items.map((item) => ({ ...item, status: "pending" })),
        required_ids: copy(fixture.required_ids),
        failed_id: fixture.failed_id,
        group_action: fixture.group_action,
        selected_ids: [],
        hovered_item_id: null,
        hover_events: 0,
        hover_control_activations: 0,
        group_action_submissions: 0,
        submitted_membership: [],
        succeeded_ids: [],
        simulated_failed_id: null,
        recovery_submissions: 0,
        recovery_ids: [],
        recovered_id: null,
      }),
      evaluate(action, taskState, event) {
        if (!["gallery-group", "gallery-recover"].includes(action)) return null;
        const expected = taskState.required_ids;
        const submitted = Array.isArray(event.selected_ids)
          ? event.selected_ids.map(String)
          : taskState.selected_ids;
        const evidence = {
          expected_membership: expected,
          submitted_membership: submitted,
          selected_ids: taskState.selected_ids,
          hovered_item_id: taskState.hovered_item_id,
          hover_events: taskState.hover_events,
          group_action: taskState.group_action,
          group_action_submissions: taskState.group_action_submissions,
          succeeded_ids: taskState.succeeded_ids,
          simulated_failed_id: taskState.simulated_failed_id,
          recovery_ids: taskState.recovery_ids,
          recovery_submissions: taskState.recovery_submissions,
          recovered_id: taskState.recovered_id,
          items: taskState.items,
        };
        if (action === "gallery-group") {
          if (taskState.group_action_submissions > 1) {
            return {
              outcome: "FAIL",
              reason: {
                code: "duplicate-group-action",
                message: "The visible group action may be performed once per attempt.",
              },
              evidence,
            };
          }
          if (new Set(submitted).size !== submitted.length) {
            return {
              outcome: "FAIL",
              reason: {
                code: "duplicate-membership",
                message: "Remove duplicate card membership and submit the exact named subset.",
              },
              evidence,
            };
          }
          if (expected.some((itemId) => !submitted.includes(itemId))) {
            return {
              outcome: "FAIL",
              reason: {
                code: "incomplete-selection",
                message: "Select every named card before using the group action.",
              },
              evidence,
            };
          }
          if (submitted.some((itemId) => !expected.includes(itemId))) {
            return {
              outcome: "FAIL",
              reason: {
                code: "wrong-membership",
                message: "Deselect cards outside the named subset before using the group action.",
              },
              evidence,
            };
          }
          if (!taskState.hovered_item_id) {
            return {
              outcome: "FAIL",
              reason: {
                code: "hover-required",
                message:
                  "Hover a representative card until its native controls are revealed first.",
              },
              evidence,
            };
          }
          return null;
        }
        if (taskState.group_action_submissions === 0) {
          return {
            outcome: "FAIL",
            reason: {
              code: "group-action-required",
              message: "Run the group action before recovering its simulated failed item.",
            },
            evidence,
          };
        }
        const recoveryIds = Array.isArray(event.item_ids)
          ? event.item_ids.map(String)
          : [String(event.item_id ?? "")];
        if (taskState.recovery_submissions > 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-recovery",
              message: "Recover the failed gallery item once only.",
            },
            evidence: { ...evidence, recovery_ids: recoveryIds },
          };
        }
        if (
          recoveryIds.length !== 1 ||
          recoveryIds.some((itemId) => taskState.succeeded_ids.includes(itemId))
        ) {
          return {
            outcome: "FAIL",
            reason: {
              code: "recovery-reapplies-successes",
              message: "Recover only the simulated failed item; leave successful items untouched.",
            },
            evidence: { ...evidence, recovery_ids: recoveryIds },
          };
        }
        if (recoveryIds[0] !== taskState.failed_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-recovery-item",
              message: "Use the recovery control on the item named as simulated failed.",
            },
            evidence: { ...evidence, recovery_ids: recoveryIds },
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "The exact group was applied once and only its failed item was recovered.",
          },
          evidence: { ...evidence, recovery_ids: recoveryIds },
        };
      },
    };
  }

  function queueDefinition() {
    const variants = [
      {
        target_id: "queue-amber-blue-hour",
        target_title: "Blue Hour",
        target_artist: "Northline",
        current_queue: [
          { id: "queue-amber-morning", title: "Morning Circuit", artist: "Field Notes" },
          { id: "queue-amber-blue-hour", title: "Blue Hour", artist: "Northline" },
          { id: "queue-amber-blue-hour-live", title: "Blue Hour", artist: "Northline Live" },
        ],
        catalog: [
          { id: "catalog-amber-blue-hour", title: "Blue Hour", artist: "Global Catalog" },
          { id: "catalog-amber-river", title: "River Signal", artist: "Northline" },
          { id: "catalog-amber-morning", title: "Morning Circuit", artist: "Field Notes" },
          { id: "catalog-amber-dusk", title: "Dusk Index", artist: "Harbor Radio" },
        ],
      },
      {
        target_id: "queue-cobalt-paper-suns",
        target_title: "Paper Suns",
        target_artist: "Cobalt Choir",
        current_queue: [
          { id: "queue-cobalt-glass", title: "Glass Orchard", artist: "Mile Marker" },
          { id: "queue-cobalt-paper-suns", title: "Paper Suns", artist: "Cobalt Choir" },
          { id: "queue-cobalt-paper-suns-demo", title: "Paper Suns", artist: "Cobalt Choir Demo" },
          { id: "queue-cobalt-hush", title: "Hush Signal", artist: "Mile Marker" },
        ],
        catalog: [
          { id: "catalog-cobalt-paper-suns", title: "Paper Suns", artist: "Catalog Session" },
          { id: "catalog-cobalt-glass", title: "Glass Orchard", artist: "Mile Marker" },
          { id: "catalog-cobalt-hush", title: "Hush Signal", artist: "Mile Marker" },
          { id: "catalog-cobalt-thread", title: "Thread Count", artist: "Cobalt Choir" },
        ],
      },
      {
        target_id: "queue-lime-night-index",
        target_title: "Night Index",
        target_artist: "Lime Atlas",
        current_queue: [
          { id: "queue-lime-salt", title: "Salt Lines", artist: "Low Meridian" },
          { id: "queue-lime-night-index", title: "Night Index", artist: "Lime Atlas" },
          { id: "queue-lime-night-index-alt", title: "Night Index", artist: "Lime Atlas (alt)" },
        ],
        catalog: [
          { id: "catalog-lime-night-index", title: "Night Index", artist: "Catalog Cut" },
          { id: "catalog-lime-salt", title: "Salt Lines", artist: "Low Meridian" },
          { id: "catalog-lime-horizon", title: "Horizon Receipt", artist: "Lime Atlas" },
          { id: "catalog-lime-static", title: "Static Garden", artist: "Low Meridian" },
        ],
      },
    ];
    return {
      task_id: "queue-keeper",
      task_version: 1,
      seed: "queue-keeper-seed-1",
      title: "Queue keeper",
      variants,
      objective: ({ fixture }) =>
        `Play “${fixture.target_title}” by ${fixture.target_artist} FROM THE CURRENT QUEUE, preserving queue order.`,
      scope: "A local mock player's current queue, broader catalog and advancing playback receipt.",
      permitted_actions:
        "Choose the named object in Current queue and press its Play control once; inspect the catalog only for contrast.",
      forbidden_effects:
        "Playing a same-title catalog object, replacing the queue or playing more than once fails the attempt.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        target_id: fixture.target_id,
        target_title: fixture.target_title,
        target_artist: fixture.target_artist,
        target_index: fixture.current_queue.findIndex((item) => item.id === fixture.target_id),
        queue: copy(fixture.current_queue),
        queue_snapshot: copy(fixture.current_queue),
        catalog: copy(fixture.catalog),
        current_track_id: fixture.current_queue[0].id,
        current_index: 0,
        playback_advances: 0,
        play_submissions: 0,
        played_source: null,
        played_object_id: null,
        queue_replaced: false,
        queue_after_play: copy(fixture.current_queue),
      }),
      evaluate(action, taskState, event) {
        if (action !== "queue-play") return null;
        const evidence = {
          target_id: taskState.target_id,
          target_title: taskState.target_title,
          target_artist: taskState.target_artist,
          clicked_source: event.source ?? null,
          clicked_object_id: event.item_id ?? null,
          expected_queue: taskState.queue_snapshot,
          queue_after_play: taskState.queue,
          queue_preserved:
            sameSequence(
              taskState.queue.map((item) => item.id),
              taskState.queue_snapshot.map((item) => item.id),
            ) && sameRecords(taskState.queue, taskState.queue_snapshot),
          current_track_id: taskState.current_track_id,
          current_index: taskState.current_index,
          target_index: taskState.target_index,
          playback_advances: taskState.playback_advances,
          play_submissions: taskState.play_submissions,
          queue_replaced: taskState.queue_replaced,
        };
        if (taskState.play_submissions > 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-play",
              message: "Play the named current-queue object once, then retry the attempt.",
            },
            evidence,
          };
        }
        if (event.source !== "queue" || event.item_id !== taskState.target_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-object-identity",
              message:
                "The same title in the broader catalog is a different object; play the named row from Current queue.",
            },
            evidence,
          };
        }
        if (!evidence.queue_preserved || taskState.queue_replaced) {
          return {
            outcome: "FAIL",
            reason: {
              code: "queue-replaced",
              message: "Playing the item must preserve every current-queue object and its order.",
            },
            evidence,
          };
        }
        if (
          taskState.current_track_id !== taskState.target_id ||
          taskState.current_index !== taskState.target_index ||
          taskState.playback_advances !== 1
        ) {
          return {
            outcome: "FAIL",
            reason: {
              code: "playback-not-advanced",
              message: "The mock player must advance once to the exact current-queue object.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "The current-queue object played once without changing queue order.",
          },
          evidence,
        };
      },
    };
  }

  function ledgerDefinition() {
    const variants = [
      {
        filter_value: "ready",
        sort_key: "amount",
        sort_direction: "desc",
        matching_ids: ["receipt-amber-103", "receipt-amber-101"],
        expected_total: 187,
        search_target_id: "receipt-amber-104",
        rows: [
          { id: "receipt-amber-101", status: "ready", amount: 72, sender: "Aster", note: "dock" },
          { id: "receipt-amber-102", status: "held", amount: 91, sender: "Birch", note: "review" },
          { id: "receipt-amber-103", status: "ready", amount: 115, sender: "Cedar", note: "seal" },
          { id: "receipt-amber-104", status: "paid", amount: 46, sender: "Dahlia", note: "closed" },
        ],
      },
      {
        filter_value: "approved",
        sort_key: "id",
        sort_direction: "asc",
        matching_ids: ["receipt-cobalt-201", "receipt-cobalt-204", "receipt-cobalt-205"],
        expected_total: 264,
        search_target_id: "receipt-cobalt-202",
        rows: [
          { id: "receipt-cobalt-205", status: "approved", amount: 88, sender: "Elm", note: "east" },
          {
            id: "receipt-cobalt-201",
            status: "approved",
            amount: 104,
            sender: "Fennel",
            note: "north",
          },
          {
            id: "receipt-cobalt-202",
            status: "pending",
            amount: 61,
            sender: "Grove",
            note: "hold",
          },
          {
            id: "receipt-cobalt-204",
            status: "approved",
            amount: 72,
            sender: "Hazel",
            note: "south",
          },
        ],
      },
      {
        filter_value: "review",
        sort_key: "amount",
        sort_direction: "asc",
        matching_ids: ["receipt-lime-303", "receipt-lime-301", "receipt-lime-304"],
        expected_total: 173,
        search_target_id: "receipt-lime-302",
        rows: [
          { id: "receipt-lime-304", status: "review", amount: 69, sender: "Indigo", note: "third" },
          {
            id: "receipt-lime-301",
            status: "review",
            amount: 58,
            sender: "Juniper",
            note: "first",
          },
          { id: "receipt-lime-302", status: "ready", amount: 42, sender: "Kite", note: "target" },
          { id: "receipt-lime-303", status: "review", amount: 46, sender: "Larch", note: "second" },
        ],
      },
    ];
    return {
      task_id: "receipt-ledger",
      task_version: 1,
      seed: "receipt-ledger-seed-1",
      title: "Receipt ledger",
      variants,
      objective: ({ fixture }) =>
        `Filter status “${fixture.filter_value}”, sort by ${fixture.sort_key} ${fixture.sort_direction}, inspect every matching row, then enter IDs ${fixture.matching_ids.join(", ")} and total ${fixture.expected_total}.`,
      scope:
        "A synthetic receipt table, an exhaustive filtered scan and a separate known-row search.",
      permitted_actions:
        "Set the visible filter and sort, inspect each matching row, enter the specified IDs and total, or use the contrast search for one known row.",
      forbidden_effects:
        "Submitting with an uninspected matching row, skipped coverage, wrong view, wrong IDs/total or duplicate answer fails the attempt.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        rows: copy(fixture.rows),
        expected_filter: fixture.filter_value,
        expected_sort_key: fixture.sort_key,
        expected_sort_direction: fixture.sort_direction,
        expected_ids: copy(fixture.matching_ids),
        expected_total: fixture.expected_total,
        search_target_id: fixture.search_target_id,
        view_filter: "all",
        view_sort_key: "id",
        view_sort_direction: "asc",
        view_changes: 0,
        inspected_ids: [],
        submitted_ids: [],
        submitted_total: "",
        answer_submissions: 0,
        search_query: "",
        search_count: 0,
        search_result: null,
      }),
      evaluate(action, taskState, event) {
        if (action !== "ledger-submit") return null;
        const missing = taskState.expected_ids.filter(
          (rowId) => !taskState.inspected_ids.includes(rowId),
        );
        const extra = taskState.inspected_ids.filter(
          (rowId) => !taskState.expected_ids.includes(rowId),
        );
        const evidence = {
          expected_filter: taskState.expected_filter,
          expected_sort: {
            key: taskState.expected_sort_key,
            direction: taskState.expected_sort_direction,
          },
          observed_view: {
            filter: taskState.view_filter,
            sort_key: taskState.view_sort_key,
            sort_direction: taskState.view_sort_direction,
            changes: taskState.view_changes,
          },
          expected_ids: taskState.expected_ids,
          inspected_ids: taskState.inspected_ids,
          missing_ids: missing,
          extra_inspected_ids: extra,
          submitted_ids: taskState.submitted_ids,
          submitted_total: taskState.submitted_total,
          expected_total: taskState.expected_total,
          answer_submissions: taskState.answer_submissions,
          search_target_id: taskState.search_target_id,
          search_result: taskState.search_result,
          search_counts_as_inspection: false,
        };
        if (taskState.answer_submissions > 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-answer",
              message: "Submit the ledger answer once after completing the scan.",
            },
            evidence,
          };
        }
        if (
          taskState.view_filter !== taskState.expected_filter ||
          taskState.view_sort_key !== taskState.expected_sort_key ||
          taskState.view_sort_direction !== taskState.expected_sort_direction
        ) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-view",
              message: "Apply the filter and sort named in the ledger brief before submitting.",
            },
            evidence,
          };
        }
        if (missing.length) {
          return {
            outcome: "FAIL",
            reason: {
              code: "uninspected-row",
              message: `Inspect every matching row before answering; missing ${missing.join(", ")}.`,
            },
            evidence,
          };
        }
        if (extra.length) {
          return {
            outcome: "FAIL",
            reason: {
              code: "unexpected-row",
              message: `The exhaustive scan must cover only matching rows; remove ${extra.join(", ")}.`,
            },
            evidence,
          };
        }
        if (!sameMembers(taskState.submitted_ids, taskState.expected_ids)) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-ids",
              message: "Enter exactly the IDs shown by the inspected matching rows.",
            },
            evidence,
          };
        }
        if (String(taskState.submitted_total) !== String(taskState.expected_total)) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-total",
              message: `Enter the exact total ${taskState.expected_total} from the matching rows.`,
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "Every matching row was inspected and the exact IDs and total were submitted.",
          },
          evidence,
        };
      },
    };
  }

  function recoveryDefinition() {
    const variants = [
      {
        job_label: "Amber ledger export",
        draft_name: "amber-ledger-final",
        payload: "18 rows · regional totals",
        existing_name: "amber-ledger-final",
        existing_payload: "11 rows · archived totals",
        rename_to: "amber-ledger-final-2",
      },
      {
        job_label: "Cobalt parcel index",
        draft_name: "cobalt-parcel-index",
        payload: "7 bundles · sealed",
        existing_name: "cobalt-parcel-index",
        existing_payload: "5 bundles · draft",
        rename_to: "cobalt-parcel-index-copy",
      },
      {
        job_label: "Lime receipt bundle",
        draft_name: "lime-receipts-weekly",
        payload: "31 receipts · verified",
        existing_name: "lime-receipts-weekly",
        existing_payload: "29 receipts · pending",
        rename_to: "lime-receipts-weekly-new",
      },
    ];
    return {
      task_id: "recovery-room",
      task_version: 1,
      seed: "recovery-room-seed-1",
      title: "Recovery room",
      variants,
      objective: ({ fixture }) =>
        `Submit ${fixture.job_label} as “${fixture.draft_name}” with data “${fixture.payload}”. If the in-page rename conflict appears, preserve that data, rename to “${fixture.rename_to}”, then resume only the unfinished step.`,
      scope:
        "One synthetic job submission, an explicitly in-page rename conflict and one resumed step.",
      permitted_actions:
        "Submit once, resolve the labeled in-page conflict with the specified new name, then resume the unfinished step.",
      forbidden_effects:
        "Do not resubmit after the conflict, lose the differing data, confirm the wrong name or resume before resolving it; no OS chooser is involved.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        job_label: fixture.job_label,
        expected_name: fixture.draft_name,
        expected_payload: fixture.payload,
        existing_name: fixture.existing_name,
        existing_payload: fixture.existing_payload,
        expected_rename: fixture.rename_to,
        name: fixture.draft_name,
        payload: fixture.payload,
        rename_input: "",
        job_submissions: 0,
        submitted_data: null,
        conflict_seen: false,
        conflict_visible: false,
        conflict_resolved: false,
        unfinished_step: "submit",
        rename_submissions: 0,
        resume_count: 0,
        resumed_data: null,
        data_preserved: false,
      }),
      evaluate(action, taskState, event) {
        if (!["recovery-submit", "recovery-confirm", "recovery-resume"].includes(action)) {
          return null;
        }
        const evidence = {
          expected_data: {
            name: taskState.expected_name,
            payload: taskState.expected_payload,
          },
          submitted_data: taskState.submitted_data,
          current_data: { name: taskState.name, payload: taskState.payload },
          conflict: {
            label: "In-page rename conflict",
            existing_data: {
              name: taskState.existing_name,
              payload: taskState.existing_payload,
            },
            visible: taskState.conflict_visible,
            seen: taskState.conflict_seen,
            resolved: taskState.conflict_resolved,
          },
          rename_input: taskState.rename_input,
          expected_rename: taskState.expected_rename,
          unfinished_step: taskState.unfinished_step,
          job_submissions: taskState.job_submissions,
          rename_submissions: taskState.rename_submissions,
          resume_count: taskState.resume_count,
          resumed_data: taskState.resumed_data,
          data_preserved: taskState.data_preserved,
          event: event.type ?? null,
        };
        if (action === "recovery-submit") {
          if (taskState.job_submissions > 1) {
            return {
              outcome: "FAIL",
              reason: {
                code: "duplicate-submission",
                message:
                  "The conflict is in-page; do not submit the job again. Resume its unfinished step.",
              },
              evidence,
            };
          }
          if (
            taskState.name !== taskState.expected_name ||
            taskState.payload !== taskState.expected_payload
          ) {
            return {
              outcome: "FAIL",
              reason: {
                code: "wrong-submission-data",
                message: "Preserve the specified job name and differing data when submitting.",
              },
              evidence,
            };
          }
          return null;
        }
        if (action === "recovery-confirm") {
          if (!taskState.conflict_seen) {
            return {
              outcome: "FAIL",
              reason: {
                code: "conflict-not-open",
                message: "Submit once and handle the labeled in-page conflict before renaming.",
              },
              evidence,
            };
          }
          if (taskState.rename_submissions > 1) {
            return {
              outcome: "FAIL",
              reason: {
                code: "duplicate-rename",
                message: "Confirm the in-page rename once, then resume the unfinished step.",
              },
              evidence,
            };
          }
          if (taskState.rename_input !== taskState.expected_rename) {
            return {
              outcome: "FAIL",
              reason: {
                code: "wrong-rename",
                message: "Use the new name specified by the in-page conflict instructions.",
              },
              evidence,
            };
          }
          return null;
        }
        if (!taskState.conflict_resolved || taskState.unfinished_step !== "resume") {
          return {
            outcome: "FAIL",
            reason: {
              code: "resume-before-resolution",
              message: "Resolve the in-page rename conflict before resuming the unfinished step.",
            },
            evidence,
          };
        }
        if (taskState.job_submissions !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-submission",
              message: "Resume the existing submission; do not create another job submission.",
            },
            evidence,
          };
        }
        if (taskState.resume_count > 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-resume",
              message: "Resume the unfinished step exactly once per attempt.",
            },
            evidence,
          };
        }
        if (!taskState.data_preserved) {
          return {
            outcome: "FAIL",
            reason: {
              code: "data-not-preserved",
              message:
                "Keep the submitted differing data intact while resolving the name conflict.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "The one submission survived the in-page rename conflict and resumed once.",
          },
          evidence,
        };
      },
    };
  }

  function changedDefinition() {
    const variants = [
      {
        desk_label: "Amber approval desk",
        old_target_id: "changed-amber-confirm-old",
        target_id: "changed-amber-confirm-new",
        target_label: "Confirm Amber approval",
        old_target_label: "Confirm legacy Amber approval",
        decoy_id: "changed-amber-preview",
        decoy_label: "Open preview (no commit)",
        update_delay_ms: 170,
      },
      {
        desk_label: "Cobalt release desk",
        old_target_id: "changed-cobalt-confirm-old",
        target_id: "changed-cobalt-confirm-new",
        target_label: "Confirm Cobalt release",
        old_target_label: "Confirm legacy Cobalt release",
        decoy_id: "changed-cobalt-preview",
        decoy_label: "Open preview (no commit)",
        update_delay_ms: 260,
      },
      {
        desk_label: "Lime dispatch desk",
        old_target_id: "changed-lime-confirm-old",
        target_id: "changed-lime-confirm-new",
        target_label: "Confirm Lime dispatch",
        old_target_label: "Confirm legacy Lime dispatch",
        decoy_id: "changed-lime-preview",
        decoy_label: "Open preview (no commit)",
        update_delay_ms: 210,
      },
    ];
    return {
      task_id: "changed-desk",
      task_version: 1,
      seed: "changed-desk-seed-1",
      title: "Changed desk",
      variants,
      objective: ({ fixture }) =>
        `On the ${fixture.desk_label}, wait for the visible control replacement, take a fresh observation, then press “${fixture.target_label}”.`,
      scope:
        "A delayed in-page layout replacement with one designated control and an adjacent non-destructive decoy.",
      permitted_actions:
        "Request the desk update, wait for its visible replacement, record a fresh observation and confirm the current target once.",
      forbidden_effects:
        "A stale target or adjacent decoy is a failed attempt; the decoy must not commit data or an action.",
      timeout_ms: DEFAULT_TIMEOUT_MS,
      initial_state: ({ fixture }) => ({
        desk_label: fixture.desk_label,
        old_target_id: fixture.old_target_id,
        old_target_label: fixture.old_target_label,
        target_id: fixture.target_id,
        target_label: fixture.target_label,
        decoy_id: fixture.decoy_id,
        decoy_label: fixture.decoy_label,
        layout_version: 1,
        layout_changed: false,
        update_requests: 0,
        update_pending: false,
        observed_layout_version: 0,
        observed_target_id: null,
        fresh_observations: 0,
        confirm_submissions: 0,
        confirmed_target_id: null,
        decoy_clicks: 0,
        decoy_effect: null,
        committed_actions: 0,
      }),
      evaluate(action, taskState, event) {
        if (action !== "changed-confirm") return null;
        const evidence = {
          desk_label: taskState.desk_label,
          layout_version: taskState.layout_version,
          layout_changed: taskState.layout_changed,
          old_target_id: taskState.old_target_id,
          intended_target_id: taskState.target_id,
          update_requests: taskState.update_requests,
          clicked_target_id: event.target_id ?? null,
          clicked_layout_version: event.layout_version ?? null,
          observed_layout_version: taskState.observed_layout_version,
          observed_target_id: taskState.observed_target_id,
          fresh_observations: taskState.fresh_observations,
          confirm_submissions: taskState.confirm_submissions,
          confirmed_target_id: taskState.confirmed_target_id,
          decoy_id: taskState.decoy_id,
          decoy_clicks: taskState.decoy_clicks,
          decoy_effect: taskState.decoy_effect,
          committed_actions: taskState.committed_actions,
        };
        if (taskState.confirm_submissions > 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "duplicate-confirm",
              message: "Confirm the replacement target once after a fresh observation.",
            },
            evidence,
          };
        }
        if (event.target_id === taskState.decoy_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "decoy-target",
              message:
                "The adjacent preview decoy is non-destructive and is not the designated target.",
            },
            evidence: { ...evidence, effect: "non-destructive: preview unchanged; no commit" },
          };
        }
        if (event.target_id === taskState.old_target_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "stale-target",
              message:
                "The control was replaced; observe the changed desk and use its current target.",
            },
            evidence,
          };
        }
        if (event.target_id !== taskState.target_id) {
          return {
            outcome: "FAIL",
            reason: {
              code: "wrong-target",
              message: "Use the designated control named by the fresh desk observation.",
            },
            evidence,
          };
        }
        if (taskState.update_requests !== 1) {
          return {
            outcome: "FAIL",
            reason: {
              code: "update-not-requested",
              message: "Request the delayed desk replacement before confirming its target.",
            },
            evidence,
          };
        }
        if (!taskState.layout_changed || event.layout_version !== taskState.layout_version) {
          return {
            outcome: "FAIL",
            reason: {
              code: "stale-layout",
              message: "Wait for the visible replacement before confirming the desk action.",
            },
            evidence,
          };
        }
        if (
          taskState.observed_layout_version !== taskState.layout_version ||
          taskState.observed_target_id !== taskState.target_id
        ) {
          return {
            outcome: "FAIL",
            reason: {
              code: "fresh-observation-required",
              message: "Record a fresh observation after the layout replacement, then confirm.",
            },
            evidence,
          };
        }
        if (
          taskState.committed_actions !== 1 ||
          taskState.confirmed_target_id !== taskState.target_id
        ) {
          return {
            outcome: "FAIL",
            reason: {
              code: "confirmation-not-committed",
              message: "The fresh observation must authorize exactly one intended confirmation.",
            },
            evidence,
          };
        }
        return {
          outcome: "PASS",
          reason: {
            code: "objective-reached",
            message: "The replaced target was freshly observed and confirmed once.",
          },
          evidence,
        };
      },
    };
  }

  const TASK_DEFINITIONS = Object.freeze([
    basicsDefinition(),
    followupDefinition(),
    dispatchDefinition(),
    parcelDefinition(),
    archiveDefinition(),
    galleryDefinition(),
    queueDefinition(),
    ledgerDefinition(),
    recoveryDefinition(),
    changedDefinition(),
  ]);
  const DEFINITION_BY_ID = Object.freeze(
    Object.fromEntries(TASK_DEFINITIONS.map((definition) => [definition.task_id, definition])),
  );

  function taskRecord(definition, suiteSeed) {
    const fixture = fixtureFor(definition, suiteSeed);
    const context = { fixture };
    return {
      task_id: definition.task_id,
      task_version: definition.task_version,
      seed: definition.seed,
      title: definition.title,
      fixture_seed: fixture?.fixture_seed || null,
      fixture: fixture ? copy(fixture) : null,
      objective: described(definition.objective, context),
      scope: described(definition.scope, context),
      permitted_actions: described(definition.permitted_actions, context),
      forbidden_effects: described(definition.forbidden_effects, context),
      timeout_ms: definition.timeout_ms,
      status: "not-started",
      draft: definition.initial_state(context),
      current_attempt: null,
      attempts: [],
      last_receipt: null,
    };
  }

  function createRunState({ run_id = id("run"), at = 0, suite_seed, seed } = {}) {
    const effectiveSuiteSeed = suite_seed ?? seed ?? DEFAULT_SUITE_SEED;
    return freeze({
      schema: SCHEMA,
      run_id,
      created_at: at,
      suite_seed: effectiveSuiteSeed,
      selected_task_id: TASK_DEFINITIONS[0].task_id,
      capabilities: copy(CAPABILITIES),
      tasks: Object.fromEntries(
        TASK_DEFINITIONS.map((definition) => [
          definition.task_id,
          taskRecord(definition, effectiveSuiteSeed),
        ]),
      ),
      run_history: [],
    });
  }

  function completionFor(state) {
    const tasks = Object.values(state.tasks);
    return {
      passed: tasks.filter((task) => task.attempts.some((attempt) => attempt.outcome === "PASS"))
        .length,
      total: tasks.length,
      first_attempt_passed: tasks.filter((task) => task.attempts[0]?.outcome === "PASS").length,
      terminal_attempts: tasks.reduce((count, task) => count + task.attempts.length, 0),
    };
  }

  function replaceTask(state, taskId, update) {
    const oldTask = state.tasks[taskId];
    const editableTask = {
      ...oldTask,
      draft: copy(oldTask.draft),
      current_attempt: copy(oldTask.current_attempt),
      attempts: [...oldTask.attempts],
    };
    const nextTask =
      typeof update === "function" ? update(editableTask) : { ...editableTask, ...update };
    return freeze({ ...state, tasks: { ...state.tasks, [taskId]: nextTask } });
  }

  function activeAttempt(task, action) {
    if (task.status !== "running" || !task.current_attempt) return null;
    if (action.attempt_id && action.attempt_id !== task.current_attempt.attempt_id) return null;
    return task.current_attempt;
  }

  function beginAttempt(state, taskId, at, attemptId) {
    const definition = DEFINITION_BY_ID[taskId];
    const context = { fixture: state.tasks[taskId].fixture };
    const initial = definition.initial_state(context);
    return replaceTask(state, taskId, (task) => ({
      ...task,
      status: "running",
      draft: copy(initial),
      current_attempt: {
        task_id: definition.task_id,
        task_version: definition.task_version,
        seed: definition.seed,
        attempt_id: attemptId || id("attempt"),
        started_at: at,
        deadline_at: at + definition.timeout_ms,
        event_count: 0,
        state: copy(initial),
      },
    }));
  }

  function receiptFor(state, task, attempt, outcome, reason, evidence, at) {
    const elapsed = Math.max(0, at - attempt.started_at);
    return freeze({
      schema: SCHEMA,
      task_id: task.task_id,
      task_version: task.task_version,
      seed: task.seed,
      fixture_seed: task.fixture_seed,
      suite_seed: state.suite_seed,
      run_id: state.run_id,
      attempt_id: attempt.attempt_id,
      outcome,
      reason: copy(reason),
      evidence: copy(evidence),
      cost: {
        wall_time_ms: elapsed,
        controller_tokens: null,
        monetary: null,
      },
    });
  }

  function finish(state, taskId, outcome, reason, evidence, at) {
    return replaceTask(state, taskId, (task) => {
      const attempt = task.current_attempt;
      if (!attempt) return task;
      const receipt = receiptFor(state, task, attempt, outcome, reason, evidence, at);
      return {
        ...task,
        status: outcome,
        draft: copy(attempt.state),
        current_attempt: null,
        last_receipt: receipt,
        attempts: [...task.attempts, receipt],
      };
    });
  }

  function updateRunning(state, taskId, action, updateState, evaluationAction = null) {
    const task = state.tasks[taskId];
    const attempt = activeAttempt(task, action);
    if (!attempt) return state;
    const definition = DEFINITION_BY_ID[taskId];
    const nextAttempt = copy(attempt);
    nextAttempt.state = updateState(copy(nextAttempt.state));
    nextAttempt.event_count += 1;
    const updated = replaceTask(state, taskId, (current) => ({
      ...current,
      draft: copy(nextAttempt.state),
      current_attempt: nextAttempt,
    }));
    if (!evaluationAction) return updated;
    const result = definition.evaluate(evaluationAction, nextAttempt.state, action);
    return result
      ? finish(updated, taskId, result.outcome, result.reason, result.evidence, action.at)
      : updated;
  }

  function timeoutRunning(state, taskId, at) {
    const task = state.tasks[taskId];
    if (
      !task ||
      task.status !== "running" ||
      !task.current_attempt ||
      at < task.current_attempt.deadline_at
    ) {
      return state;
    }
    return finish(
      state,
      taskId,
      "FAIL",
      { code: "timeout", message: "The running attempt reached its deadline." },
      {
        deadline_at: task.current_attempt.deadline_at,
        observed_at: at,
        state: task.current_attempt.state,
      },
      at,
    );
  }

  function updateField(state, taskId, action, field) {
    const task = state.tasks[taskId];
    if (["PASS", "FAIL", "ABORTED"].includes(task.status)) return state;
    const value = String(action.value ?? "");
    if (task.status === "running") {
      return updateRunning(state, taskId, action, (current) => ({ ...current, [field]: value }));
    }
    return replaceTask(state, taskId, (current) => ({
      ...current,
      draft: { ...current.draft, [field]: value },
    }));
  }

  function reduce(state, action) {
    if (!action || typeof action.type !== "string") return state;
    const at = action.at === undefined ? 0 : action.at;
    if (action.type === "SELECT_TASK" && DEFINITION_BY_ID[action.task_id]) {
      return freeze({ ...copy(state), selected_task_id: action.task_id });
    }
    if (action.type === "RESET_RUN") {
      const fresh = createRunState({
        run_id: action.run_id || id("run"),
        at,
        suite_seed: action.suite_seed ?? action.seed ?? state.suite_seed,
      });
      return freeze({
        ...fresh,
        run_history: [
          ...copy(state.run_history),
          { run_id: state.run_id, completion: completionFor(state), tasks: copy(state.tasks) },
        ],
      });
    }
    if (action.type === "TICK") {
      return Object.keys(state.tasks).reduce(
        (next, taskId) => timeoutRunning(next, taskId, at),
        state,
      );
    }
    const taskId = action.task_id || state.selected_task_id;
    if (!DEFINITION_BY_ID[taskId]) return state;
    const task = state.tasks[taskId];

    if (action.type === "START" && task.status === "not-started") {
      return beginAttempt(state, taskId, at, action.attempt_id);
    }
    if (action.type === "RETRY" && ["PASS", "FAIL", "ABORTED"].includes(task.status)) {
      return beginAttempt(state, taskId, at, action.attempt_id);
    }
    if (action.type === "RESET_TASK") {
      const definition = DEFINITION_BY_ID[taskId];
      const initial = definition.initial_state({ fixture: task.fixture });
      // A running attempt ends with a receipt of its own; resetting over it
      // would let the next attempt pass as the first.
      const settled =
        task.status === "running" && task.current_attempt
          ? finish(
              state,
              taskId,
              "ABORTED",
              {
                code: "task-reset",
                cause: "reset",
                message: "The task was reset while this attempt was running.",
              },
              { state: task.current_attempt.state },
              at,
            )
          : state;
      return replaceTask(settled, taskId, (current) => ({
        ...current,
        status: "not-started",
        draft: copy(initial),
        current_attempt: null,
      }));
    }
    if (action.type === "ABORT") {
      const attempt = activeAttempt(task, action);
      if (!attempt) return state;
      const cause = action.cause === "runner-cancelled" ? "runner-cancelled" : "user";
      return finish(
        state,
        taskId,
        "ABORTED",
        {
          code: cause === "user" ? "user-abort" : "runner-cancellation",
          cause,
          message:
            cause === "user"
              ? "The user stopped the running attempt."
              : "The runner cancelled the attempt.",
        },
        { state: attempt.state },
        at,
      );
    }
    if (action.type === "FORBIDDEN_EFFECT") {
      const attempt = activeAttempt(task, action);
      if (!attempt) return state;
      return finish(
        state,
        taskId,
        "FAIL",
        {
          code: "forbidden-effect",
          message: "The attempt performed an explicitly forbidden effect.",
        },
        { effect: action.effect || "unspecified", state: attempt.state },
        at,
      );
    }
    if (
      taskId === "native-basics" &&
      (action.type === "ENTRY_INPUT" || action.type === "NOTES_INPUT")
    ) {
      const field = action.type === "ENTRY_INPUT" ? "entry" : "notes";
      return updateField(state, taskId, action, field);
    }
    if (taskId === "native-basics" && action.type === "APPLY") {
      const entry = String(
        action.entry ?? task.current_attempt?.state.entry ?? task.draft.entry ?? "",
      );
      const notes = String(
        action.notes ?? task.current_attempt?.state.notes ?? task.draft.notes ?? "",
      );
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({ ...current, entry, notes, applications: current.applications + 1 }),
        "apply",
      );
    }
    if (taskId === "native-followup" && action.type === "READY_REQUEST") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        ready: false,
        readiness_requests: current.readiness_requests + 1,
      }));
    }
    if (taskId === "native-followup" && action.type === "READY") {
      return updateRunning(state, taskId, action, (current) => ({ ...current, ready: true }));
    }
    if (taskId === "native-followup" && action.type === "BOTTOM") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({ ...current, bottom: current.bottom + 1 }),
        "bottom",
      );
    }
    if (taskId === "dispatch-desk" && action.type === "DISPATCH_DESTINATION_INPUT") {
      return updateField(state, taskId, action, "destination");
    }
    if (taskId === "dispatch-desk" && action.type === "DISPATCH_QUANTITY_INPUT") {
      return updateField(state, taskId, action, "quantity");
    }
    if (taskId === "dispatch-desk" && action.type === "DISPATCH_NOTES_INPUT") {
      return updateField(state, taskId, action, "notes");
    }
    if (taskId === "dispatch-desk" && action.type === "DISPATCH_SUBMIT") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          dispatch_submissions: current.dispatch_submissions + 1,
          dispatch_pending: true,
        }),
        "dispatch-submit",
      );
    }
    if (taskId === "dispatch-desk" && action.type === "DISPATCH_RECEIPT") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          dispatch_pending: false,
          dispatch_receipt: {
            kind: "dispatch",
            submission_count: current.dispatch_submissions,
            destination: current.destination,
            quantity: current.quantity,
            notes: current.notes,
          },
        }),
        "dispatch-receipt",
      );
    }
    if (taskId === "delayed-parcel" && action.type === "PARCEL_RECEIPT_INPUT") {
      return updateField(state, taskId, action, "acknowledgement_input");
    }
    if (taskId === "delayed-parcel" && action.type === "PARCEL_START_JOB") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          job_id: current.expected_job_id,
          job_starts: current.job_starts + 1,
          ready: false,
          ready_job_id: null,
        }),
        "parcel-start-job",
      );
    }
    if (taskId === "delayed-parcel" && action.type === "PARCEL_READY") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        ready: true,
        ready_job_id: String(action.job_id ?? current.job_id ?? ""),
      }));
    }
    if (taskId === "delayed-parcel" && action.type === "PARCEL_ACK") {
      const jobId = String(action.job_id ?? "");
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          acknowledgement_count: current.acknowledgement_count + 1,
          acknowledged_job_ids: [...current.acknowledged_job_ids, jobId],
          parcel_receipt: {
            kind: "parcel-acknowledgement",
            job_id: jobId,
            ready: current.ready,
          },
        }),
        "parcel-ack",
      );
    }
    if (taskId === "archive-elevator" && action.type === "ELEVATOR_OUTER_SCROLL") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        outer_scroll: numberOr(action.scroll_top, current.outer_scroll),
        target_reached: false,
        target_receipt: null,
      }));
    }
    if (taskId === "archive-elevator" && action.type === "ELEVATOR_INNER_SCROLL") {
      return updateRunning(state, taskId, action, (current) => {
        const scrollTop = numberOr(action.scroll_top, current.inner_scroll);
        const innerScroll = action.pane_id === current.pane_id ? scrollTop : current.inner_scroll;
        return {
          ...current,
          inner_scroll: innerScroll,
          inner_progress: percentage(
            (innerScroll + current.inner_viewport) / (current.inner_depth + current.inner_viewport),
          ),
          target_reached: false,
          target_receipt: null,
        };
      });
    }
    if (taskId === "archive-elevator" && action.type === "ELEVATOR_TARGET_VISIBLE") {
      return updateRunning(state, taskId, action, (current) => {
        const reached =
          action.visible === true &&
          action.pane_id === current.pane_id &&
          action.target_id === current.target_id;
        return {
          ...current,
          target_reached: reached,
          target_receipt: {
            pane_id: action.pane_id ?? null,
            target_id: action.target_id ?? null,
            reached,
            progress: numberOr(action.progress, current.inner_progress),
          },
        };
      });
    }
    if (taskId === "archive-elevator" && action.type === "ELEVATOR_BOTTOM") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({ ...current, bottom_clicks: current.bottom_clicks + 1 }),
        "elevator-bottom",
      );
    }
    if (taskId === "gallery-crew" && action.type === "GALLERY_HOVER") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        hovered_item_id: String(action.item_id ?? ""),
        hover_events: current.hover_events + 1,
      }));
    }
    if (taskId === "gallery-crew" && action.type === "GALLERY_HOVER_CONTROL") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        hover_control_activations: current.hover_control_activations + 1,
      }));
    }
    if (taskId === "gallery-crew" && action.type === "GALLERY_SELECT") {
      const itemId = String(action.item_id ?? "");
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        selected_ids: current.selected_ids.includes(itemId)
          ? current.selected_ids.filter((selectedId) => selectedId !== itemId)
          : [...current.selected_ids, itemId],
      }));
    }
    if (taskId === "gallery-crew" && action.type === "GALLERY_GROUP_ACTION") {
      const selectedIds = Array.isArray(action.selected_ids)
        ? action.selected_ids.map(String)
        : [...task.current_attempt.state.selected_ids];
      return updateRunning(
        state,
        taskId,
        action,
        (current) => {
          const succeededIds = current.required_ids.filter(
            (itemId) => itemId !== current.failed_id,
          );
          const accepted =
            current.group_action_submissions === 0 &&
            unique(selectedIds).length === selectedIds.length &&
            sameMembers(selectedIds, current.required_ids) &&
            Boolean(current.hovered_item_id);
          return {
            ...current,
            group_action_submissions: current.group_action_submissions + 1,
            submitted_membership: selectedIds,
            succeeded_ids: accepted ? succeededIds : current.succeeded_ids,
            simulated_failed_id: accepted ? current.failed_id : current.simulated_failed_id,
            items: accepted
              ? current.items.map((item) =>
                  item.item_id === current.failed_id
                    ? { ...item, status: "simulated-failed" }
                    : succeededIds.includes(item.item_id)
                      ? { ...item, status: "succeeded" }
                      : item,
                )
              : current.items,
          };
        },
        "gallery-group",
      );
    }
    if (taskId === "gallery-crew" && action.type === "GALLERY_RECOVER") {
      const recoveryIds = Array.isArray(action.item_ids)
        ? action.item_ids.map(String)
        : [String(action.item_id ?? "")];
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          recovery_submissions: current.recovery_submissions + 1,
          recovery_ids: recoveryIds,
          recovered_id:
            current.group_action_submissions === 1 && recoveryIds.length === 1
              ? recoveryIds[0]
              : null,
          items:
            current.group_action_submissions === 1 &&
            recoveryIds.length === 1 &&
            recoveryIds[0] === current.failed_id
              ? current.items.map((item) =>
                  item.item_id === current.failed_id ? { ...item, status: "recovered" } : item,
                )
              : current.items,
        }),
        "gallery-recover",
      );
    }
    if (taskId === "queue-keeper" && action.type === "QUEUE_PLAY") {
      const source = action.source === "catalog" ? "catalog" : "queue";
      const itemId = String(action.item_id ?? "");
      return updateRunning(
        state,
        taskId,
        action,
        (current) => {
          const collection = source === "queue" ? current.queue : current.catalog;
          const selected = collection.find((item) => item.id === itemId);
          const replacedQueue = source === "catalog" && selected ? [copy(selected)] : current.queue;
          const selectedIndex =
            source === "queue" ? collection.findIndex((item) => item.id === itemId) : 0;
          return {
            ...current,
            play_submissions: current.play_submissions + 1,
            played_source: source,
            played_object_id: itemId,
            current_track_id: itemId,
            current_index: selectedIndex,
            playback_advances: current.playback_advances + 1,
            queue_replaced: source === "catalog" && Boolean(selected),
            queue: replacedQueue,
            queue_after_play: copy(replacedQueue),
          };
        },
        "queue-play",
      );
    }
    if (taskId === "receipt-ledger" && action.type === "LEDGER_VIEW") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        view_filter: String(action.filter ?? current.view_filter),
        view_sort_key: String(action.sort_key ?? current.view_sort_key),
        view_sort_direction: String(action.sort_direction ?? current.view_sort_direction),
        view_changes: current.view_changes + 1,
      }));
    }
    if (taskId === "receipt-ledger" && action.type === "LEDGER_IDS_INPUT") {
      return updateField(state, taskId, action, "submitted_ids_input");
    }
    if (taskId === "receipt-ledger" && action.type === "LEDGER_TOTAL_INPUT") {
      return updateField(state, taskId, action, "submitted_total_input");
    }
    if (taskId === "receipt-ledger" && action.type === "LEDGER_SEARCH_INPUT") {
      return updateField(state, taskId, action, "search_query");
    }
    if (taskId === "receipt-ledger" && action.type === "LEDGER_INSPECT") {
      const rowId = String(action.row_id ?? "");
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        inspected_ids: current.inspected_ids.includes(rowId)
          ? current.inspected_ids
          : [...current.inspected_ids, rowId],
      }));
    }
    if (taskId === "receipt-ledger" && action.type === "LEDGER_SEARCH") {
      const rowId = String(action.row_id ?? task.current_attempt?.state.search_query ?? "");
      const found = rowId === task.current_attempt?.state.search_target_id;
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        search_query: rowId,
        search_count: current.search_count + 1,
        search_result: {
          mode: "target-search",
          row_id: rowId,
          found,
          counts_as_inspection: false,
        },
      }));
    }
    if (taskId === "receipt-ledger" && action.type === "LEDGER_SUBMIT") {
      const submittedIds = Array.isArray(action.ids) ? action.ids.map(String) : [];
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          submitted_ids: submittedIds,
          submitted_total: String(action.total ?? ""),
          answer_submissions: current.answer_submissions + 1,
        }),
        "ledger-submit",
      );
    }
    if (taskId === "recovery-room" && action.type === "RECOVERY_NAME_INPUT") {
      return updateField(state, taskId, action, "name");
    }
    if (taskId === "recovery-room" && action.type === "RECOVERY_PAYLOAD_INPUT") {
      return updateField(state, taskId, action, "payload");
    }
    if (taskId === "recovery-room" && action.type === "RECOVERY_RENAME_INPUT") {
      return updateField(state, taskId, action, "rename_input");
    }
    if (taskId === "recovery-room" && action.type === "RECOVERY_SUBMIT") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          job_submissions: current.job_submissions + 1,
          submitted_data: { name: current.name, payload: current.payload },
          conflict_seen: true,
          conflict_visible: true,
          conflict_resolved: false,
          unfinished_step: "resolve-conflict",
          data_preserved: false,
        }),
        "recovery-submit",
      );
    }
    if (taskId === "recovery-room" && action.type === "RECOVERY_CONFIRM") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          rename_submissions: current.rename_submissions + 1,
          conflict_visible: false,
          conflict_resolved: current.rename_input === current.expected_rename,
          unfinished_step:
            current.rename_input === current.expected_rename ? "resume" : current.unfinished_step,
          data_preserved: Boolean(
            current.submitted_data &&
              current.submitted_data.name === current.expected_name &&
              current.submitted_data.payload === current.payload,
          ),
        }),
        "recovery-confirm",
      );
    }
    if (taskId === "recovery-room" && action.type === "RECOVERY_RESUME") {
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          resume_count: current.resume_count + 1,
          resumed_data: { name: current.rename_input, payload: current.payload },
          data_preserved: Boolean(
            current.submitted_data &&
              current.submitted_data.name === current.expected_name &&
              current.submitted_data.payload === current.payload &&
              current.payload === current.expected_payload,
          ),
        }),
        "recovery-resume",
      );
    }
    if (taskId === "changed-desk" && action.type === "CHANGED_SCHEDULE") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        update_requests: current.update_requests + 1,
        update_pending: true,
      }));
    }
    if (taskId === "changed-desk" && action.type === "CHANGED_LAYOUT") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        layout_version: 2,
        layout_changed: true,
        update_pending: false,
      }));
    }
    if (taskId === "changed-desk" && action.type === "CHANGED_OBSERVE") {
      return updateRunning(state, taskId, action, (current) => ({
        ...current,
        observed_layout_version: numberOr(action.layout_version, 0),
        observed_target_id: String(action.target_id ?? ""),
        fresh_observations: current.fresh_observations + 1,
      }));
    }
    if (taskId === "changed-desk" && action.type === "CHANGED_CONFIRM") {
      const targetId = String(action.target_id ?? "");
      return updateRunning(
        state,
        taskId,
        action,
        (current) => ({
          ...current,
          confirm_submissions: current.confirm_submissions + 1,
          confirmed_target_id: targetId,
          decoy_clicks: current.decoy_clicks + (targetId === current.decoy_id ? 1 : 0),
          decoy_effect:
            targetId === current.decoy_id
              ? "non-destructive: preview unchanged; no commit"
              : current.decoy_effect,
          committed_actions:
            targetId === current.target_id
              ? current.committed_actions + 1
              : current.committed_actions,
        }),
        "changed-confirm",
      );
    }
    return state;
  }

  return {
    SCHEMA,
    TASK_DEFINITIONS,
    createRunState,
    createInitialState: createRunState,
    completionFor,
    reduce,
  };
});
