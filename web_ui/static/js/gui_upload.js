/* Experimental presentation of the existing upload process. */
(() => {
  const { useState, useEffect, useRef } = React;
  const buttonClass =
    "rounded-lg border [border-color:var(--ua-border)] px-3 py-2 text-sm font-semibold hover:opacity-80 disabled:opacity-40 disabled:cursor-not-allowed";
  const inputClass =
    "w-full min-w-0 rounded-lg border [border-color:var(--ua-border)] bg-transparent px-3 py-2 text-sm";
  const quoteArgument = (value) => `'${String(value).replace(/'/g, "'\\''")}'`;
  const commonOptions = [
    {
      flag: "--tmdb",
      aliases: ["--tmdb", "-tmdb"],
      label: "TMDb ID",
      placeholder: "Auto-detect, or movie/12345",
    },
    {
      flag: "--imdb",
      aliases: ["--imdb", "-imdb"],
      label: "IMDb ID",
      placeholder: "Auto-detect, or tt0123456",
    },
  ];

  // Retain the rest of the argument string verbatim, including quoted paths.
  const argumentTokens = (args) => [
    ...args.matchAll(/(?:[^\s'"\\]|\\.|'(?:[^']*)'|"(?:\\.|[^"\\])*")+/g),
  ];
  const unquote = (token) =>
    token.replace(
      /'([^']*)'|"((?:\\.|[^"\\])*)"|\\(.)/g,
      (_, single, double, escaped) =>
        single ??
        (double !== undefined ? double.replace(/\\(["\\])/g, "$1") : escaped),
    );
  const readOption = (args, aliases, boolean = false) => {
    const tokens = argumentTokens(args);
    let value = boolean ? false : "";
    tokens.forEach((token, index) => {
      const text = unquote(token[0]);
      if (aliases.includes(text))
        value = boolean
          ? true
          : tokens[index + 1] && !tokens[index + 1][0].startsWith("-")
            ? unquote(tokens[index + 1][0])
            : "";
      else if (
        !boolean &&
        aliases.some((alias) => text.startsWith(`${alias}=`))
      )
        value = text.slice(text.indexOf("=") + 1);
    });
    return value;
  };
  const changeOption = (args, aliases, value, boolean = false) => {
    const tokens = argumentTokens(args);
    const spans = [];
    tokens.forEach((token, index) => {
      const text = unquote(token[0]);
      if (
        aliases.includes(text) ||
        aliases.some((alias) => text.startsWith(`${alias}=`))
      ) {
        let end = token.index + token[0].length;
        if (
          !boolean &&
          aliases.includes(text) &&
          tokens[index + 1] &&
          !tokens[index + 1][0].startsWith("-")
        )
          end = tokens[index + 1].index + tokens[index + 1][0].length;
        spans.push([token.index, end]);
      }
    });
    let result = args;
    spans.reverse().forEach(([start, end]) => {
      result = result.slice(0, start) + result.slice(end);
    });
    result = result.trim();
    return [
      result,
      value ? `${aliases[0]}${boolean ? "" : ` ${quoteArgument(value)}`}` : "",
    ]
      .filter(Boolean)
      .join(" ");
  };

  function Options({ args, onChange, presets }) {
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {commonOptions.map((option) => (
            <label key={option.flag} className="block text-sm font-semibold">
              {option.label}
              <input
                className={`${inputClass} mt-1 font-normal`}
                placeholder={option.placeholder}
                value={readOption(args, option.aliases)}
                onChange={(event) =>
                  onChange(
                    changeOption(args, option.aliases, event.target.value),
                  )
                }
              />
            </label>
          ))}
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={readOption(args, ["--debug", "-debug"], true)}
            onChange={(event) =>
              onChange(
                changeOption(
                  args,
                  ["--debug", "-debug"],
                  event.target.checked,
                  true,
                ),
              )
            }
          />
          <span>
            <strong>Debug run</strong>
            <span className="block text-xs opacity-70">
              Prepares the release without submitting it to trackers. Images may
              still be uploaded.
            </span>
          </span>
        </label>
        <details className="text-sm">
          <summary className="cursor-pointer font-semibold py-1">
            Advanced arguments &amp; presets
          </summary>
          <div className="space-y-2 pt-2">
            <input
              aria-label="Additional arguments"
              className={inputClass}
              value={args}
              onChange={(event) => onChange(event.target.value)}
              placeholder="Optional upload arguments"
            />
            {presets}
          </div>
        </details>
      </div>
    );
  }

  function Corrections({ busy, onAnswer }) {
    const [values, setValues] = useState({});
    const [advanced, setAdvanced] = useState("");
    const fields = [
      ...commonOptions,
      { flag: "--tag", label: "Release group", placeholder: "e.g. NTb" },
      { flag: "--edition", label: "Edition", placeholder: "e.g. Extended" },
    ];
    const args = [
      ...fields
        .filter(({ flag }) => values[flag]?.trim())
        .map(({ flag }) => `${flag} ${quoteArgument(values[flag].trim())}`),
      advanced.trim(),
    ]
      .filter(Boolean)
      .join(" ");
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (args) onAnswer(args);
        }}
        className="space-y-3"
      >
        <p className="text-sm opacity-70">
          Fill in the values you want to change. Leave the others blank to keep
          them.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {fields.map(({ flag, label, placeholder }) => (
            <label className="text-sm font-semibold" key={flag}>
              {label}
              <input
                className={`${inputClass} mt-1 font-normal`}
                placeholder={placeholder}
                value={values[flag] || ""}
                onChange={(event) =>
                  setValues({ ...values, [flag]: event.target.value })
                }
                disabled={busy}
              />
            </label>
          ))}
        </div>
        <details>
          <summary className="text-sm cursor-pointer">
            Other corrections
          </summary>
          <input
            aria-label="Other correction arguments"
            value={advanced}
            onChange={(event) => setAdvanced(event.target.value)}
            placeholder="e.g. --category tv"
            className={`${inputClass} mt-2`}
            disabled={busy}
          />
        </details>
        <div className="flex flex-wrap gap-2">
          <button
            className={`${buttonClass} ua-accent-action bg-blue-600 border-blue-600 text-white`}
            disabled={busy || !args}
          >
            Update details
          </button>
          <button
            type="button"
            className={buttonClass}
            disabled={busy}
            onClick={() => onAnswer("continue")}
          >
            Keep current details
          </button>
        </div>
      </form>
    );
  }

  function TrackerLabel({ tracker, favicon }) {
    const [failedFavicon, setFailedFavicon] = useState(null);
    return (
      <span className="flex items-start gap-2">
        {favicon && favicon !== failedFavicon ? (
          <img
            src={favicon}
            alt=""
            width="16"
            height="16"
            className="w-4 h-4 mt-0.5 shrink-0 rounded-sm object-contain"
            onError={() => setFailedFavicon(favicon)}
          />
        ) : (
          <span
            className="w-4 h-4 mt-0.5 shrink-0 rounded-sm bg-black/10 text-center text-[9px] leading-4 opacity-70"
            aria-hidden="true"
          >
            {tracker.charAt(0)}
          </span>
        )}
        <span className="min-w-0">{tracker}</span>
      </span>
    );
  }

  function getAdditionalReviewFields(review, media) {
    const aliases = {
      episodetitle: "episode",
      episodeoverview: "overview",
      subcategory: "releasetype",
      auxiliary: "auxiliaryfiles",
      audibleurl: "audible",
      audio: "technical",
    };
    const fieldKey = (label) => {
      const key = String(label || "")
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "");
      return aliases[key] || key;
    };
    const visibleLabels = new Set();
    if (media) {
      [
        ["Title", media.title || media.name || media.filename],
        ["Category", media.category],
        ["Overview", media.episode_overview || media.overview],
        ["Genre", media.genres?.length],
        ["Cover", media.poster_url],
        ["Resolution", media.resolution],
        ["Source", media.source],
        ["Audio", media.audio],
        ["TMDb", media.tmdb],
        ["IMDb", media.imdb],
      ].forEach(([label, value]) => {
        if (value) visibleLabels.add(fieldKey(label));
      });
      (media.metadata_sources || []).forEach((source) => {
        if (source.value) {
          visibleLabels.add(fieldKey(source.key));
          visibleLabels.add(fieldKey(source.label));
        }
      });
      (media.detail_sections || []).forEach((section) => {
        (section.items || []).forEach((item) => {
          if (item.value) visibleLabels.add(fieldKey(item.label));
        });
      });
    }
    // Keep details absent from the metadata panel, without repeating warnings
    // which already appear above the release name.
    return (review.fields || []).filter(
      ({ label, value }) =>
        !visibleLabels.has(fieldKey(label)) &&
        !(review.notices || []).some(
          (notice) => notice.text === `${label}: ${value}`,
        ),
    );
  }

  function ReleaseReview({ review, trackers = [], media }) {
    const trackerIcons = new Map(
      trackers.map((tracker) => [tracker.name.toUpperCase(), tracker.favicon]),
    );
    const additionalFields = getAdditionalReviewFields(review, media);
    return (
      <div className="space-y-4" data-testid="release-review">
        {!!review.flags?.length && (
          <div className="flex flex-wrap gap-2" aria-label="Release flags">
            {review.flags.map((flag) => (
              <span
                key={flag}
                className="ua-accent-chip inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-semibold"
              >
                <span
                  className="ua-accent-indicator h-1.5 w-1.5 rounded-full"
                  aria-hidden="true"
                />
                {flag}
              </span>
            ))}
          </div>
        )}
        {!!review.notices?.length && (
          <div className="space-y-2" aria-label="Release notices">
            {review.notices.map((notice, index) => (
              <p
                key={index}
                className={`rounded-lg border p-3 text-sm break-words ${notice.tone === "warning" ? "border-amber-500/60" : "[border-color:var(--ua-border)] opacity-80"}`}
              >
                {notice.tone === "warning" && (
                  <strong className="block text-xs uppercase tracking-wide mb-1 text-amber-500">
                    Please check
                  </strong>
                )}
                {notice.text}
              </p>
            ))}
          </div>
        )}
        <div className="space-y-1">
          <h4 className="text-sm font-semibold">Base name</h4>
          <p className="text-sm break-words [overflow-wrap:anywhere]">
            {review.base_name || "Not available"}
          </p>
        </div>
        {!!additionalFields.length && (
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            {additionalFields.map(({ label, value }, index) => (
              <div key={`${label}-${index}`} className="min-w-0">
                <dt className="text-xs opacity-70 mb-1">{label}</dt>
                <dd className="break-words [overflow-wrap:anywhere]">
                  {/^https?:\/\/\S+$/i.test(value) ? (
                    <a
                      className="ua-accent-link hover:underline"
                      href={value}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {value}
                    </a>
                  ) : (
                    value
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {!!review.tracker_names?.length && (
          <div className="rounded-lg border [border-color:var(--ua-border)] overflow-hidden">
            <table className="w-full table-fixed text-sm">
              <caption className="sr-only">Tracker release names</caption>
              <thead className="bg-black/10">
                <tr className="border-b [border-color:var(--ua-border)]">
                  <th scope="col" className="w-2/5 sm:w-1/4 p-3 text-left">
                    Tracker
                  </th>
                  <th scope="col" className="p-3 text-left">
                    Release name
                  </th>
                </tr>
              </thead>
              <tbody>
                {review.tracker_names.map(({ tracker, name }) => (
                  <tr
                    key={tracker}
                    className="border-t [border-color:var(--ua-border)]"
                  >
                    <th
                      scope="row"
                      className="p-3 text-left align-top font-semibold break-words [overflow-wrap:anywhere]"
                    >
                      <TrackerLabel
                        tracker={tracker}
                        favicon={trackerIcons.get(tracker.toUpperCase())}
                      />
                    </th>
                    <td className="p-3 align-top break-words [overflow-wrap:anywhere]">
                      {name}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  function duplicateReviewProgress(review, progress) {
    const queue = progress.filter((item) => item.group === "duplicate_review");
    const current = queue.find((item) => item.label === review.tracker);
    const position = Number(review.position || current?.current || 1);
    const total = Math.max(
      position,
      queue.filter(
        (item) =>
          item.current > 0 || ["queued", "reviewing"].includes(item.status),
      ).length,
    );
    return {
      position,
      total,
      searching: queue.some((item) => item.status === "searching"),
    };
  }

  function DuplicateReview({ review, trackers = [], progress = [] }) {
    const favicon = trackers.find(
      (tracker) => tracker.name.toUpperCase() === review.tracker.toUpperCase(),
    )?.favicon;
    const formatSize = (bytes) => {
      if (!Number.isFinite(bytes) || bytes <= 0) return "Not available";
      const units = ["B", "KiB", "MiB", "GiB", "TiB"];
      const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 4);
      return `${(bytes / 1024 ** index).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${units[index]}`;
    };
    const signed = (value) => `${value >= 0 ? "+" : ""}${value}`;
    const counter = duplicateReviewProgress(review, progress);
    const matchCount = review.entries?.length || 0;
    return (
      <div className="space-y-3 text-sm" data-testid="duplicate-review">
        <div className="flex flex-wrap items-start justify-between gap-2 font-semibold">
          <TrackerLabel tracker={review.tracker} favicon={favicon} />
          <div
            className="text-right text-xs font-normal opacity-70"
            aria-label="Duplicate review progress"
            aria-live="polite"
          >
            <p className="font-semibold tabular-nums">
              Tracker {counter.position} of {counter.total}
              {counter.searching ? "+" : ""}
            </p>
            <p>
              {matchCount} {matchCount === 1 ? "match" : "matches"} to review
            </p>
            {counter.searching && <p>More tracker checks running…</p>}
          </div>
        </div>
        {(review.notices || []).map((notice, index) => (
          <p
            key={index}
            className="rounded-lg border border-amber-500/60 p-3 break-words"
          >
            {notice}
          </p>
        ))}
        <div className="rounded-lg bg-black/5 p-3 space-y-1">
          <h4 className="text-xs font-semibold opacity-70">Your upload</h4>
          <p className="break-words [overflow-wrap:anywhere]">
            {review.upload_name}
          </p>
          <p className="text-xs opacity-70">
            Size: {formatSize(review.upload_size)}
          </p>
        </div>
        <ul className="space-y-2" aria-label="Existing releases">
          {(review.entries || []).map((entry, index) => (
            <li
              key={index}
              className="rounded-lg border [border-color:var(--ua-border)] p-3 flex flex-col sm:flex-row sm:items-start gap-3"
            >
              <div className="min-w-0 flex-1 space-y-1">
                <p className="font-semibold break-words [overflow-wrap:anywhere]">
                  {entry.name || "Unnamed release"}
                </p>
                {/^https?:\/\/\S+$/i.test(entry.url || "") && (
                  <a
                    href={entry.url}
                    className="ua-accent-link inline-block hover:underline"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    View on {review.tracker} ↗
                  </a>
                )}
              </div>
              <dl className="flex flex-wrap gap-x-5 gap-y-2 sm:text-right shrink-0">
                <div>
                  <dt className="text-xs opacity-70 mb-1">Size</dt>
                  <dd>{formatSize(entry.size)}</dd>
                </div>
                {review.show_size_difference && (
                  <div>
                    <dt className="text-xs opacity-70 mb-1">Difference</dt>
                    <dd className="font-semibold tabular-nums">
                      {entry.difference
                        ? `${signed(entry.difference.mb)} MB / ${signed(entry.difference.percent)}%`
                        : "Not available"}
                    </dd>
                  </div>
                )}
              </dl>
            </li>
          ))}
        </ul>
        {review.show_size_difference && (
          <p className="text-xs opacity-70">
            Size differences compare each existing release with your upload.
            Positive means larger; negative means smaller.
          </p>
        )}
      </div>
    );
  }

  function UploadReview({ review, trackers = [] }) {
    return (
      <ul className="space-y-2 text-sm" aria-label="Trackers ready for upload">
        {review.trackers.map(({ tracker, detail }) => (
          <li
            key={tracker}
            className="rounded-lg border [border-color:var(--ua-border)] p-3 space-y-1"
          >
            <TrackerLabel
              tracker={tracker}
              favicon={
                trackers.find(
                  (item) => item.name.toUpperCase() === tracker.toUpperCase(),
                )?.favicon
              }
            />
            <p className="opacity-70">{detail}</p>
          </li>
        ))}
      </ul>
    );
  }

  function CheckReview({ review, trackers = [] }) {
    const favicon = trackers.find(
      (tracker) => tracker.name.toUpperCase() === review.tracker.toUpperCase(),
    )?.favicon;
    return (
      <div className="space-y-3 text-sm" data-testid="check-review">
        <div className="font-semibold">
          <TrackerLabel tracker={review.tracker} favicon={favicon} />
        </div>
        {review.kind === "duplicate" && (
          <p>
            The duplicate search could not be completed. Existing releases have
            not been confirmed.
          </p>
        )}
        {review.messages?.length ? (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold opacity-70">
              Reported during this check
            </h4>
            <ul className="space-y-2" aria-label="Check messages">
              {review.messages.map((message, index) => (
                <li
                  key={index}
                  className="rounded-lg border border-amber-500/60 p-3 whitespace-pre-wrap break-words [overflow-wrap:anywhere]"
                >
                  {message
                    .split(/(https?:\/\/[^\s<>"']+)/gi)
                    .map((part, partIndex) =>
                      /^https?:\/\//i.test(part) &&
                      !part.includes("[REDACTED]") ? (
                        <a
                          key={partIndex}
                          href={part}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="ua-accent-link hover:underline"
                        >
                          {part}
                        </a>
                      ) : (
                        part
                      ),
                    )}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="rounded-lg border border-amber-500/60 p-3">
            The tracker did not provide a detailed reason. Check Console for any
            additional information before continuing.
          </p>
        )}
      </div>
    );
  }

  function ChoiceCard({ choice, busy, onAnswer }) {
    const [posterFailed, setPosterFailed] = useState(false);
    const hasPoster = Object.prototype.hasOwnProperty.call(
      choice,
      "poster_url",
    );
    const showPoster =
      !posterFailed && /^https?:\/\//i.test(choice.poster_url || "");
    return (
      <div
        className="ua-gui-choice flex items-start gap-3 rounded-lg border p-3"
        data-disabled={busy || undefined}
      >
        {hasPoster && (
          <div className="w-14 sm:w-16 aspect-[2/3] shrink-0 overflow-hidden rounded bg-black/10 flex items-center justify-center">
            {showPoster ? (
              <img
                src={choice.poster_url}
                alt=""
                width="128"
                height="192"
                loading="lazy"
                decoding="async"
                className="w-full h-full object-cover"
                onError={() => setPosterFailed(true)}
              />
            ) : (
              <span
                className="text-center text-xs opacity-50 px-1"
                aria-hidden="true"
              >
                No poster
              </span>
            )}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <button
            type="button"
            aria-label={`Select ${choice.label}`}
            className="ua-gui-choice-select flex w-full items-center justify-between gap-3 text-left text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed"
            disabled={busy}
            onClick={() => onAnswer(choice.value)}
          >
            <span className="min-w-0 break-words">{choice.label}</span>
            <span
              className="rounded-lg border [border-color:var(--ua-border)] px-3 py-2 shrink-0"
              aria-hidden="true"
            >
              Select
            </span>
          </button>
          {choice.detail && (
            <p className="text-xs opacity-70 mt-1">{choice.detail}</p>
          )}
          {/^https?:\/\//i.test(choice.url || "") && (
            <a
              href={choice.url}
              className="ua-gui-choice-link ua-accent-link text-xs hover:underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              View on TMDb ↗
            </a>
          )}
        </div>
      </div>
    );
  }

  function Question({
    prompt,
    busy,
    onAnswer,
    onConsole,
    context,
    trackers,
    media,
    progress,
  }) {
    const [answer, setAnswer] = useState("");
    const focusRef = useRef(null);
    useEffect(() => {
      focusRef.current?.focus({ preventScroll: true });
    }, []);
    const hasDefault = prompt.default !== null && prompt.default !== undefined;
    return (
      <section
        className="rounded-xl border border-amber-500/60 p-4 space-y-3"
        aria-labelledby="gui-question"
      >
        <div className="text-xs font-semibold uppercase tracking-wider text-amber-500">
          Your input is needed
        </div>
        <h3
          id="gui-question"
          ref={focusRef}
          tabIndex={-1}
          className="text-lg font-semibold outline-none"
        >
          {prompt.upload_review
            ? "Ready to upload"
            : prompt.review
              ? "Review release details"
              : prompt.duplicate_review
                ? {
                    exact: "Exact match found",
                    season_pack: "Season pack found",
                    trumpable: "Trumpable releases found",
                  }[prompt.duplicate_review.kind] ||
                  "Potential duplicates found"
                : prompt.check_review
                  ? {
                      duplicate: "Duplicate check failed",
                      rules: "Review upload checks",
                    }[prompt.check_review.kind] || "Upload checks failed"
                  : prompt.question}
        </h3>
        {prompt.upload_review && (
          <UploadReview review={prompt.upload_review} trackers={trackers} />
        )}
        {prompt.review && (
          <ReleaseReview
            review={prompt.review}
            trackers={trackers}
            media={media}
          />
        )}
        {prompt.duplicate_review && (
          <DuplicateReview
            review={prompt.duplicate_review}
            trackers={trackers}
            progress={progress}
          />
        )}
        {prompt.check_review && (
          <CheckReview review={prompt.check_review} trackers={trackers} />
        )}
        {context && !prompt.review && (
          <details
            key={prompt.id}
            open={
              !prompt.upload_review &&
              !prompt.duplicate_review &&
              !prompt.check_review &&
              (prompt.kind === "yes_no" || prompt.kind === "text")
            }
            className="text-sm"
          >
            <summary className="cursor-pointer opacity-70">
              Details from the uploader
            </summary>
            <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/10 p-3 font-sans text-xs leading-relaxed">
              {context}
            </pre>
          </details>
        )}
        {prompt.kind === "console" ? (
          <div className="space-y-3">
            <p className="text-sm opacity-70">
              This question does not yet have a graphical control. Answer it in
              Console, then switch back here.
            </p>
            <button className={buttonClass} onClick={onConsole}>
              Open Console
            </button>
          </div>
        ) : prompt.kind === "yes_no" ? (
          <div className="space-y-3">
            {(prompt.upload_review ||
              prompt.review ||
              prompt.duplicate_review ||
              prompt.check_review) && (
              <p className="font-semibold">{prompt.question}</p>
            )}
            <div className="flex gap-2">
              <button
                className={`${buttonClass} ua-accent-action bg-blue-600 border-blue-600 text-white`}
                disabled={busy}
                onClick={() => onAnswer("yes")}
              >
                Yes
              </button>
              <button
                className="rounded-lg border border-red-600 bg-red-600 hover:bg-red-700 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40 disabled:cursor-not-allowed"
                disabled={busy}
                onClick={() => onAnswer("no")}
              >
                No
              </button>
            </div>
          </div>
        ) : prompt.kind === "arguments" ? (
          <Corrections busy={busy} onAnswer={onAnswer} />
        ) : (
          <div className="space-y-3">
            {prompt.kind === "choice" && (
              <div className="max-h-72 overflow-auto space-y-2">
                <p className="text-sm opacity-70">
                  Select an option below to continue.
                </p>
                {(prompt.choices || []).map((choice) => (
                  <ChoiceCard
                    key={`${choice.value}:${choice.poster_url || ""}`}
                    choice={choice}
                    busy={busy}
                    onAnswer={onAnswer}
                  />
                ))}
              </div>
            )}
            {(prompt.kind !== "choice" || prompt.allow_custom) && (
              <form
                className="flex flex-wrap gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  onAnswer(answer);
                }}
              >
                <label className="flex-1 min-w-0 text-sm">
                  {prompt.custom_label || "Your answer"}
                  {prompt.kind === "choice" ? " (optional)" : ""}
                  <input
                    className={`${inputClass} mt-1`}
                    value={answer}
                    disabled={busy}
                    onChange={(event) => setAnswer(event.target.value)}
                    placeholder={
                      hasDefault ? String(prompt.default) : "Enter a value"
                    }
                  />
                </label>
                <button
                  className={`${buttonClass} self-end ua-accent-action bg-blue-600 border-blue-600 text-white`}
                  disabled={busy || (!answer && prompt.kind === "choice")}
                >
                  Continue
                </button>
              </form>
            )}
            {(prompt.kind === "choice" || hasDefault) && (
              <button
                className={buttonClass}
                disabled={busy}
                onClick={() => onAnswer("")}
              >
                {prompt.empty_label ||
                  (hasDefault
                    ? `Use default: ${prompt.default}`
                    : "Skip selection")}
              </button>
            )}
          </div>
        )}
        {busy && (
          <p className="text-sm opacity-70" role="status">
            Sending your answer…
          </p>
        )}
      </section>
    );
  }

  function Warnings({ items }) {
    if (!items.length) return null;
    return (
      <aside
        className="mt-3 rounded-lg border border-amber-500/60 p-3 text-sm"
        aria-label="Warnings"
        aria-live="polite"
      >
        <h4 className="font-semibold text-amber-500">Warnings</h4>
        <ul className="mt-2 space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]"
            >
              {item.label !== "Warning" && (
                <p className="font-semibold">{item.label}</p>
              )}
              <p className="opacity-80">
                {item.tracker ? `${item.tracker}: ` : ""}
                {item.detail}
              </p>
            </li>
          ))}
        </ul>
      </aside>
    );
  }

  function mergeTrackerResults(results, progress, running) {
    const rows = new Map(results.map((item) => [item.tracker, item]));
    progress
      .filter((item) => item.group === "tracker")
      .forEach((item) => {
        rows.set(item.label, {
          tracker: item.label,
          outcome: item.status,
          detail: item.detail,
          url: item.url,
        });
      });
    return [...rows.values()].map((item) =>
      !running &&
      [
        "Waiting",
        "Checking…",
        "Preparing…",
        "Uploading…",
        "Debug processing…",
      ].includes(item.outcome)
        ? {
            ...item,
            outcome: "Not completed",
            detail: "The run ended before this tracker reported a result.",
            url: "",
          }
        : item,
    );
  }

  function Panel({
    running,
    prompt,
    busy,
    error,
    media,
    progress,
    result,
    context,
    onAnswer,
    onConsole,
    trackers,
  }) {
    const current = running ? media : result?.media || media;
    // The preview endpoint can return 404 as the finished session is removed.
    // Keep its last progress snapshot independent of those background polls.
    const displayedProgress =
      !running && Array.isArray(result?.media?.progress)
        ? result.media.progress
        : progress;
    const trackerResults = mergeTrackerResults(
      current?.tracker_results || [],
      displayedProgress,
      running,
    );
    const warnings = displayedProgress.filter(
      (item) => item.group === "warning",
    );
    const runWarnings = warnings.filter(
      (item) =>
        !item.tracker ||
        !trackerResults.some((row) => row.tracker === item.tracker),
    );
    const active = running && !prompt && !media?.awaiting_input;
    const activity = displayedProgress
      .filter((item) => item.group === "activity")
      .sort((a, b) => Number(a.updated_at || 0) - Number(b.updated_at || 0));
    const liveActivity = [...activity]
      .reverse()
      .find((item) => item.status === "running");
    const title = prompt
      ? "Ready for your review"
      : running
        ? "Upload in progress"
        : result?.code === 0
          ? "Run finished"
          : "Run stopped";
    return (
      <div
        className="ua-gui-upload min-h-0 min-w-0 flex-1 overflow-auto space-y-4 pb-4"
        data-testid="gui-upload"
      >
        {(running || result) && (
          <section className="rounded-xl border [border-color:var(--ua-border)] p-4">
            <div className="flex items-center gap-2">
              <span
                className={`h-2 w-2 rounded-full ${prompt ? "bg-amber-400" : running ? "bg-blue-500 animate-pulse" : "bg-gray-400"}`}
              />
              <h3 className="font-semibold" role="status">
                {title}
              </h3>
              <span className="ml-auto text-xs opacity-60">GUI preview</span>
            </div>
            <p className="text-sm opacity-70 mt-2">
              {prompt
                ? "Check the details below before continuing."
                : running
                  ? "Progress and questions will appear here as the uploader works."
                  : "Review the reported results below. The console has the complete run details."}
            </p>
            <Warnings items={runWarnings} />
            {active && (
              <div
                className="flex items-center gap-2 mt-3 text-sm"
                role="status"
                data-testid="upload-activity"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  className="shrink-0 animate-spin motion-reduce:animate-none"
                  aria-hidden="true"
                >
                  <circle
                    cx="12"
                    cy="12"
                    r="9"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    opacity="0.2"
                  />
                  <path
                    d="M12 3a9 9 0 0 1 9 9"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    strokeLinecap="round"
                  />
                </svg>
                <span>
                  {liveActivity?.label ||
                    (trackerResults.some((item) => item.outcome === "Checking…")
                      ? "Checking trackers…"
                      : trackerResults.some(
                            (item) => item.outcome === "Uploading…",
                          )
                        ? "Uploading to trackers…"
                        : "The uploader is working…")}
                </span>
              </div>
            )}
            {current && (
              <div className="mt-3 pt-3 border-t [border-color:var(--ua-border)] space-y-2">
                <p className="font-semibold break-words">
                  {current.title || current.name || current.filename}
                  {current.title && current.year ? ` (${current.year})` : ""}
                </p>
                {current.name && current.title && (
                  <p className="text-xs opacity-70 break-words">
                    {current.name}
                  </p>
                )}
                <p className="text-sm opacity-70">
                  {[
                    current.category,
                    current.resolution,
                    current.source,
                    current.audio,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
            )}
          </section>
        )}
        {error && (
          <p
            role="alert"
            className="rounded-lg border border-red-500 p-3 text-sm text-red-500"
          >
            {error}
          </p>
        )}
        {prompt && running && (
          <Question
            key={prompt.id}
            prompt={prompt}
            busy={busy}
            onAnswer={onAnswer}
            onConsole={onConsole}
            context={context}
            trackers={trackers}
            media={current}
            progress={displayedProgress}
          />
        )}
        {running && !prompt && media?.awaiting_input && (
          <section className="rounded-xl border [border-color:var(--ua-border)] p-4 text-sm space-y-2">
            <p>
              The uploader may need an answer. If no question appears here,
              check Console.
            </p>
            <button className={buttonClass} onClick={onConsole}>
              Open Console
            </button>
          </section>
        )}
        {!!activity.length && (
          <section
            className="rounded-xl border [border-color:var(--ua-border)] p-4 space-y-3"
            aria-label="Upload activity"
          >
            <h3 className="font-semibold">Activity</h3>
            {activity.slice(-6).map((item) => {
              const percent =
                item.total > 0 && Number.isFinite(item.current)
                  ? Math.max(
                      0,
                      Math.min(100, (item.current / item.total) * 100),
                    )
                  : null;
              return (
                <div key={item.id} className="text-sm space-y-1">
                  <div className="flex justify-between gap-3">
                    <span>{item.label}</span>
                    <span className="opacity-70">
                      {item.status === "completed"
                        ? "Done"
                        : item.status === "failed"
                          ? "Incomplete"
                          : !running
                            ? "Stopped"
                            : !active
                              ? "Waiting for input"
                              : percent !== null
                                ? `${Math.round(percent)}%`
                                : "Working…"}
                    </span>
                  </div>
                  {percent !== null && (
                    <progress
                      aria-label={item.label}
                      value={percent}
                      max="100"
                      className="w-full h-1.5 accent-blue-500"
                    />
                  )}
                  {item.detail && (
                    <p className="text-xs opacity-70 break-words">
                      {item.detail}
                    </p>
                  )}
                </div>
              );
            })}
          </section>
        )}
        {!!trackerResults.length && (
          <section className="rounded-xl border [border-color:var(--ua-border)] p-4 space-y-2">
            <h3 className="font-semibold">Tracker results</h3>
            {trackerResults.map(({ tracker, outcome, detail, url }) => (
              <div
                className="flex flex-wrap justify-between gap-3 text-sm"
                key={tracker}
                data-tracker={tracker}
              >
                <div className="min-w-0 flex-1">
                  <TrackerLabel
                    tracker={tracker}
                    favicon={
                      trackers.find(
                        (item) =>
                          item.name.toUpperCase() === tracker.toUpperCase(),
                      )?.favicon
                    }
                  />
                  {url &&
                    outcome === "Uploaded" &&
                    /^https?:\/\//i.test(url) && (
                      <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="ua-accent-link inline-block ml-3 hover:underline"
                      >
                        View torrent ↗
                      </a>
                    )}
                  <Warnings
                    items={warnings.filter((item) => item.tracker === tracker)}
                  />
                  {detail && (
                    <p className="mt-1 text-xs opacity-70 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                      {detail}
                    </p>
                  )}
                </div>
                <span
                  className={`shrink-0 ${
                    outcome === "Uploaded" || outcome === "Debug completed"
                      ? "text-green-500"
                      : outcome === "Failed"
                        ? "text-red-500"
                        : "opacity-70"
                  }`}
                >
                  {outcome}
                </span>
              </div>
            ))}
          </section>
        )}
      </div>
    );
  }

  window.UAGuidedUpload = {
    Panel,
    mergeTrackerResults,
    Options,
    quoteArgument,
    readOption,
    changeOption,
  };
})();
