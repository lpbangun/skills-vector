/* Role Atlas renderer, adapted from the approved concept. All role data comes from role/2 reads. */
(function () {
  "use strict";

  var ROLES = {}, GRAPH = {}, INDUSTRY = {}, KINDS = {}, envelopes = {};
  var STATUS = {
    core: ["core", "established", "Repeated posting demand at these levels."],
    rising: ["rising", "growing", "Demand is up, and an independent source agrees."],
    early: ["early signal", "emerging", "A watch item, not a confirmed trend."],
    fading: ["fading", "fading", "Asked less often. Official maps may still list it."]
  };
  var timingClasses = { leading: "lead", coincident: "now", lagging: "lag", "long-term": "long" };
  var state = { role: null, level: null, tab: "map", have: {}, lens: "level", edges: "co", node: null,
    industry: null, mapLevel: 0, mode: "list", skill: null };

  function noun(value, word) { return Number(value) === 1 ? word : word + "s"; }
  function signalReading(signal) {
    return signal.source_kind === "postings" ? signal.reading.replace(/\b1 employers\b/g, "1 employer").replace(/\b1 postings\b/g, "1 posting") : signal.reading;
  }
  function percentage(value) { return Math.round(value * 1000) / 10; }
  function levelLabel(value) { return value === null ? "level unassigned" : (role().raw.data_kind === "research" ? "first observed at L" : "first asked at L") + value; }
  function shareLabel(value) { return value === null ? "—" : (Math.round(value * 10) / 10) + "%"; }
  function comparableShares(a, b) { return a !== null && b !== null; }
  function hasOfficial(skill) { return skill.source_links.some(function (link) { return link !== "P"; }); }
  function timingTag(timing, asOf) {
    return '<span class="tm tm-' + timingClasses[timing] + '">' + esc(timing + (asOf ? " · rated " + asOf.slice(0, 4) : "")) + '</span>';
  }
  function findData(root, key, value) {
    return Array.from(document.querySelectorAll(root + " [data-" + key + "]")).find(function (element) { return element.dataset[key] === value; });
  }

  function buildViews(payloads) {
    ROLES = {}; GRAPH = {}; INDUSTRY = {}; KINDS = {}; envelopes = {};
    payloads.forEach(function (envelope) {
      var raw = envelope.role, key = raw.id.replace(/^role\./, ""), f = raw.foundation, link = raw.ladder.linked_roles[0];
      envelopes[key] = envelope;
      ROLES[key] = {
        raw: raw, label: raw.label, status: raw.status, publication: raw.publication, anchors: raw.anchors,
        foundation: { wage: f.median_pay, wageNote: f.pay_note, employment: f.employment,
          employmentNote: f.employment_note, outlook: f.outlook_10y, outlookNote: f.outlook_note, tasks: f.core_tasks },
        window: { current: raw.demand_window.id, previous: raw.demand_window.compared_to,
          postings: raw.demand_window.postings, employers: raw.demand_window.employers },
        levels: raw.ladder.levels.map(function (level) {
          return { L: level.level, name: level.typical_titles.length ? level.name : "L" + level.level, titles: level.typical_titles, years: level.years_wording,
            scope: level.scope, owns: level.owns, stepUp: level.step_up, n: level.evidence.postings, e: level.evidence.employers,
            exp: level.expectations.map(function (item) { return [item.label, item.introduced_at === null ? "unknown" : item.introduced_at === level.level ? "new" : "carry"]; }) };
        }),
        branch: link ? { from: link.from_level, label: link.role.split("-").map(function (word) {
          return raw.aliases.includes(word.toUpperCase()) ? word.toUpperCase() : word[0].toUpperCase() + word.slice(1);
        }).join(" "), note: link.note } : null,
        shifts: raw.skill_map.skills.filter(function (skill) { return skill.status !== "core"; }).map(function (skill) {
          return { id: skill.id, label: skill.label, state: STATUS[skill.status][1], status: skill.status, levels: skill.windows[skill.windows.length - 1].levels };
        })
      };
      var rows = {}, families = [], start = 0;
      [true, false].forEach(function (official) {
        var counts = {};
        raw.skill_map.skills.filter(function (skill) { return hasOfficial(skill) === official; }).forEach(function (skill) {
          rows[skill.id] = start + (counts[skill.first_level] || 0);
          counts[skill.first_level] = (counts[skill.first_level] || 0) + 1;
        });
        var height = Math.max(0, ...Object.values(counts));
        if (height) { families.push([official ? "Official map" : "Posting-led", start, start + height - 1]); start += height; }
      });
      var layout = envelope.presentation && envelope.presentation.data_kind === raw.data_kind && envelope.presentation.graph;
      if (layout && raw.skill_map.skills.every(function (skill) { return Number.isInteger(layout.rows[skill.id]); })) {
        rows = layout.rows; families = layout.families;
      }
      GRAPH[key] = { mid: raw.skill_map.top_level_adds.core_level, families: families,
        nodes: raw.skill_map.skills.map(function (skill) { return [skill.id, skill.short_label, skill.label, skill.first_level,
          rows[skill.id], STATUS[skill.status][1], skill.share_by_level, skill.source_links, skill.status, skill.disputed]; }),
        co: raw.skill_map.asked_together.map(function (edge) { return [edge.a, edge.b, edge.lift, edge.postings, edge.employers]; }),
        builds: raw.skill_map.builds_on.map(function (edge) { return [edge.a, edge.b]; }) };
      var list = raw.industries.map(function (sector) {
        var p = sector.profile;
        return { key: sector.naics, label: sector.label, naics: sector.naics, naicsTitle: sector.naics_title,
          sub: sector.sub_industries, postings: sector.evidence.postings, employers: sector.evidence.employers,
          thin: p === null, reason: sector.reason, share: p ? p.share_of_occupation_jobs : "—", pay: p ? p.median_pay : "—",
          sources: p ? p.sources : [], differs: p ? p.how_it_differs : "", duties: p ? p.distinct_duties : [], know: p ? p.domain_knowledge : [],
          certs: p ? p.certifications.map(function (cert) { return [cert.name, cert.issuer, cert.asked_as, percentage(cert.posting_share)]; }) : [] };
      });
      var heatIds = [...new Set(raw.industries.flatMap(function (sector) { return sector.profile ? sector.profile.skill_shares.map(function (item) { return item.skill; }) : []; }))];
      INDUSTRY[key] = { list: list, heat: heatIds.map(function (id) { return [id, raw.industries.map(function (sector) {
        var item = sector.profile && sector.profile.skill_shares.find(function (share) { return share.skill === id; });
        return item ? percentage(item.share) : null;
      })]; }) };
      raw.skill_map.skills.forEach(function (skill) { skill.evidence.forEach(function (signal) {
        KINDS[signal.source_kind + ":" + signal.source] = { name: signal.source, fam: signal.family,
          tm: [timingClasses[signal.timing], signal.timing] };
      }); });
    });
  }

  function skills() {
    return role().raw.skill_map.skills.map(function (skill) {
      var fams = {}; skill.evidence.forEach(function (item) { fams[item.family] = true; });
      return { id: skill.id, short: skill.short_label, label: skill.label, from: skill.first_level,
        sh: skill.share_by_level, src: skill.source_links, status: skill.status,
        sig: skill.evidence.map(function (item) { return [item.source_kind + ":" + item.source, signalReading(item), item.direction, item.family]; }),
        fams: fams, official: hasOfficial(skill), disputed: skill.disputed,
        call: skill.forecast };
    });
  }

  function renderHead() {
    var r = role(), raw = r.raw, f = r.foundation, a = r.anchors[0], envelope = envelopes[state.role];
    var official = raw.foundation.sources.find(function (source) { return source.source_kind === "onet_ratings"; });
    var projection = raw.foundation.sources.find(function (source) { return source.source_kind === "bls_projections"; });
    var experimental = raw.publication === "experimental";
    $("fixtureCallout").hidden = raw.data_kind !== "fixture";
    $("fixtureNote").textContent = envelope.fixture_caveat ? envelope.fixture_caveat + " " + raw.limits[raw.limits.length - 1] : "";
    $("releaseLabel").textContent = raw.release_id + " · " + raw.data_kind;
    $("dataFooter").textContent = "Skills Vector · Role Atlas · " + raw.data_kind + " data · " + raw.release_id + ".";
    $("roleHead").innerHTML = '<div style="display:grid; gap:0.7rem">' +
      '<div class="role-title"><h3 style="font-size:1.5rem">' + esc(r.label) + '</h3><span class="chip st-' + r.status + '">role ' + esc(r.status) + '</span>' +
      (experimental ? '<span class="chip st-emerging">experimental</span>' : '') + '</div>' +
      (experimental ? '<p class="caveat" id="experimentalCaveat"><strong>Experimental.</strong> This emerging role has no exact official occupation match. The anchor to ' + esc(a.title) + ' is partial; levels, skills and forecasts rest mostly on postings. Expect larger changes between releases.</p>' : '') +
      '<div class="mono muted">' + esc(a.system + " " + a.code + " · " + a.title + " · fit: " + a.fit) + '</div>' +
      '<div class="label">Core tasks from O*NET ' + (official ? timingTag(official.timing, official.as_of) : '') + '</div><ul class="tasks">' +
      f.tasks.map(function (task) { return '<li>' + esc(task) + '</li>'; }).join('') + '</ul></div>' +
      '<div style="display:grid; gap:0.8rem; align-content:start"><div class="label">Foundation · ' + (projection ? 'BLS ' : 'not recorded ') + (projection ? timingTag(projection.timing, projection.as_of) : '') + '</div>' +
      '<div class="found">' + [[f.wage, f.wageNote], [f.employment, f.employmentNote], [f.outlook, f.outlookNote]].map(function (item) {
        return '<div><span class="v">' + esc(item[0]) + '</span><span class="label">' + esc(item[1]) + '</span></div>';
      }).join('') + '</div><div class="label">Demand window ' + esc(r.window.current) + ' ' + timingTag(raw.demand_window.timing) + '</div>' +
      '<div class="num"><strong>' + r.window.postings + '</strong> admitted ' + noun(r.window.postings, 'posting') + ' from <strong>' + r.window.employers + '</strong> ' + noun(r.window.employers, 'employer') + '. Largest employer: ' + percentage(raw.demand_window.max_employer_share) + '%.</div></div>';
  }

  function renderSkillPanel(list) {
    var k = list.find(function (item) { return item.id === state.skill; });
    if (!k) { $("skillPanel").innerHTML = '<p class="empty">' + (role().window.postings === 0 ? "No admitted postings in this role/window. No skill evidence is available; this is not evidence of no market demand." : "No skills at this level.") + '</p>'; return; }
    var raw = role().raw, original = raw.skill_map.skills.find(function (item) { return item.id === k.id; });
    var st = STATUS[k.status], byId = {}; list.forEach(function (item) { byId[item.id] = item; });
    var pairs = coEdges().filter(function (edge) { return edge[0] === k.id || edge[1] === k.id; }).sort(function (a, b) { return b[2] - a[2]; });
    var confirming = original.evidence.filter(function (signal) { return (signal.family === "O" || signal.family === "M") && signal.direction === "up"; });
    var why = {
      core: "Repeated posting demand at its measured levels." + (k.official ? " An official map also lists it." : " It is not in official maps yet."),
      rising: "Posting share is up, and " + confirming.length + (confirming.length === 1 ? " independent source points up." : " independent sources point up."),
      early: "A watch item: independent upward confirmation or comparable-window employer coverage is not yet sufficient.",
      fading: "Posting share is down. Official maps may still list this skill because their ratings lag."
    }[k.status];
    var forecast = original.forecast;
    $("skillPanel").innerHTML = '<div style="display:grid; gap:0.4rem"><div class="label">Skill</div><h3>' + esc(k.label) + '</h3>' +
      '<div class="chips"><span class="chip st-' + st[1] + '">' + st[0] + '</span><span class="chip src">' + levelLabel(k.from) + '</span></div>' +
      '<p style="font-size:0.9rem">' + esc(why) + '</p>' + (k.disputed ? '<p class="caveat"><strong>Disputed.</strong> ' + esc(k.disputed) + '</p>' : '') + '</div>' +
      '<div><div class="label">Evidence</div><ul class="sig">' + original.evidence.map(function (signal) {
        return '<li><span class="src">' + esc(signal.source) + timingTag(signal.timing, signal.as_of) + '</span><span>' + esc(signalReading(signal)) + ' <span class="mono">' + esc(signal.family + " · " + signal.direction) + '</span></span></li>';
      }).join('') + '</ul></div>' +
      (forecast ? '<div class="forecast">' + (forecast.basis === "none"
        ? '<span class="label">No forecast yet</span><p class="forecast-reason">' + esc(forecast.reason) + '</p><p>' + esc(forecast.claim) + '</p>'
        : '<span class="label">Forecast · resolves ' + esc(forecast.resolves) + '</span><span><span class="p">' + percentage(forecast.probability) + '%</span> likely: ' + esc(forecast.claim) + '.</span><small>Base rate: ' + percentage(forecast.base_rate) + '%.' +
          (forecast.basis === "fixture" ? ' Both rates are illustrative; no backtest result is claimed.' : ' Basis: historical backtest.') + '</small>') +
        '<small>Horizon: ' + forecast.horizon_quarters + ' quarters. Resolves ' + esc(forecast.resolves) + '. Scored against ' + esc(forecast.scored_against) + '.</small></div>' :
        '<div class="forecast"><span class="label">No forecast</span><small>Core skills are re-checked each window. A qualifying drop moves a skill to fading.</small></div>') +
      '<div><div class="label">Share of postings by level</div><div class="bars">' + role().levels.map(function (level) {
        var share = k.sh[level.L - 1]; return '<div class="win"><span>L' + level.L + '</span><span class="track"><span class="bar cur" style="width:' + (share === null ? 0 : share) + '%"></span></span><span>' + shareLabel(share) + '</span></div>';
      }).join('') + '</div></div>' +
      (pairs.length ? '<div><div class="label">Most often asked together with</div><ul class="pairs">' + pairs.map(function (edge) {
        var other = byId[edge[0] === k.id ? edge[1] : edge[0]];
        return '<li><span>' + esc(other.label) + '</span><span class="mono">' + edge[2].toFixed(1) + '× · ' + edge[3] + ' postings</span></li>';
      }).join('') + '</ul></div>' : '') +
      '<a class="citation" href="/v1/expectations/' + encodeURIComponent(k.id) + '@' + encodeURIComponent(raw.release_id) + '">Pinned skill citation</a>';
  }

  function renderTabs() {
    Array.from($("tabs").children).forEach(function (button) {
      var on = button.dataset.tab === state.tab;
      button.setAttribute("aria-selected", String(on)); button.tabIndex = on ? 0 : -1;
      $("tab-" + button.dataset.tab).hidden = !on;
    });
  }

  function recordJSON() { return role().raw; }

  function renderTrackRecord() {
    var report = envelopes[state.role].track_record;
    $("ledgerTracked").textContent = report.ledger_selected ? report.tracked_count : "—";
    $("ledgerResolved").textContent = report.ledger_selected ? report.resolved_count : "—";
    $("ledgerBrier").textContent = report.brier_score === null ? "—" : report.brier_score.toFixed(3);
    $("ledgerSummary").textContent = (report.ledger_selected ? report.pending_count + " pending; " + report.scored_count + " scored; " + report.unscored_count + " unscored. " : "No ledger selected. ") + report.reason;
    $("ledgerBacktest").textContent = report.backtest.status + ": " + report.backtest.reason;
    $("ledgerCalibration").innerHTML = report.scored_count ? report.calibration_bins.filter(function (bin) { return bin.n > 0; }).map(function (bin) {
      return '<div class="cal-row"><span>' + percentage(bin.lower) + "–" + percentage(bin.upper) + '%</span><span class="cal-track" aria-hidden="true"><span class="cal-bar" style="width:' + percentage(bin.observed_rate) + '%"></span><span class="cal-tick" style="left:' + percentage(bin.midpoint) + '%"></span></span><span>' + percentage(bin.observed_rate) + "% observed · mean p " + percentage(bin.mean_probability) + "% · n=" + bin.n + "</span></div>";
    }).join("") + '<p class="rule-note">Bars: observed frequency. Ticks: bin midpoints. Left-closed bins; 100% is in the last bin.</p>' : '<p class="empty">No scored research forecasts. Calibration needs resolved calls with backtest probabilities, not illustrative or missing probabilities.</p>';
  }

  function renderMethod() {
    renderTrackRecord();
    var raw = role().raw, method = envelopes[state.role].method;
    $("limitsList").innerHTML = raw.limits.map(function (limit) { return "<li>" + esc(limit) + "</li>"; }).join("");
    $("methodWindows").textContent = "Settled window length: " + method.window_length + ". Forecast horizon: " + method.forecast_horizon_quarters + " quarters. Numeric research probabilities require a historical backtest; none-basis calls are never shown as percentages.";
    $("sourceTable").innerHTML = method.sources.map(function (source) {
      return '<tr data-source-kind="' + esc(source.source_kind) + '"><td>' + esc(source.source) + '</td><td>' + timingTag(source.timing) + '</td><td>' + esc({P: "Postings", O: "Official map", M: "Market trend", context: "Context only"}[source.family]) + '</td><td>' + esc(source.good_for) + '</td></tr>';
    }).join("");
    var layerRows = [
      ["Foundation", "baseline", "Official-map sources and occupation context.", "Long-run tasks and anchor statistics.", raw.foundation.core_tasks.length + " recorded core tasks."],
      ["Demand", "what employers ask", raw.demand_window.window_length + " windows.", "Posting and employer sample counts.", raw.demand_window.postings + " " + noun(raw.demand_window.postings, "posting") + " / " + raw.demand_window.employers + " " + noun(raw.demand_window.employers, "employer") + "."],
      ["Shifts", "forward-looking", "Comparable windows and independent signals.", "Core, rising, early and fading; disputes remain flags.", raw.skill_map.skills.filter(function (skill) { return skill.status !== "core"; }).length + " non-core skills with forecasts."],
      ["Seniority ladder", "levels of one job", "Scope and ownership first; seniority/years stay weak. Customer segments in titles count only under a reviewed role-specific policy.", "Role-specific names on the shared L1–L5 scale.", raw.ladder.levels.length + " recorded IC levels; management is a separate track."],
      ["Skills graph", "how skills connect", "Posting shares, map links and qualified co-occurrence edges.", "Level coverage, asked-together pairs and top-level additions.", raw.skill_map.skills.length + " skills / " + raw.skill_map.asked_together.length + " qualified pairs."],
      ["Industry view", "same job, different field", "Broad NAICS sectors with explicit sample floors.", "Duties, credentials, domain knowledge and skill shares.", raw.industries.length + " sectors, including visible profile gaps."],
      ["Your gap", "private, local", "Your own checkboxes, in this browser only.", "A local comparison, never a hiring or personal suitability prediction.", "No personal evidence is uploaded or persisted."]
    ];
    $("layers").innerHTML = '<div class="layer head" role="row">' + ["Layer", "Source and cadence", "What it can say", "This release"].map(function (text) {
      return '<div role="columnheader"><span class="label">' + text + '</span></div>';
    }).join('') + '</div>' + layerRows.map(function (row) {
      return '<div class="layer" role="row"><div role="cell"><span class="layer-name">' + esc(row[0]) + '</span><span class="layer-tag">' + esc(row[1]) + '</span></div>' + row.slice(2).map(function (text) {
        return '<div role="cell">' + esc(text) + '</div>';
      }).join('') + '</div>';
    }).join('');
    $("triageScope").innerHTML = '<span class="label">Recorded level scope · not a posting quote</span><h3>' + esc(level().name) + '</h3><p>' + esc(level().scope) + '</p><p><strong>Owns:</strong> ' + esc(level().owns) + '</p><p class="muted">Years wording (weak): ' + esc(level().years) + '</p>';
    $("triageLadder").innerHTML = '<table><caption class="label">Scope and ownership across this role</caption><thead><tr><th>Level</th><th>Owns</th><th>Postings</th><th>Employers</th></tr></thead><tbody>' + role().levels.map(function (item) {
      return '<tr><th scope="row">L' + item.L + ' · ' + esc(item.name) + '</th><td>' + esc(item.owns) + '</td><td>' + item.n + '</td><td>' + item.e + '</td></tr>';
    }).join('') + '</tbody></table>';
  }

  async function fetchJSON(url) {
    var response = await fetch(url, { headers: { Accept: "application/json" } });
    var payload = await response.json();
    if (!response.ok || payload.status !== "ok") throw new Error(payload.detail || "The catalog request failed.");
    return payload;
  }

  async function loadCatalog() {
    $("atlasContent").hidden = true; $("loadStatus").hidden = false; $("retryLoad").hidden = true;
    $("app").dataset.ready = "false";
    $("loadTitle").textContent = "Loading the role catalog";
    try {
      var requested = new URLSearchParams(location.search).get("release");
      var index = await fetchJSON("/v1/roles" + (requested === null ? "" : "?release=" + encodeURIComponent(requested)));
      if (!index.roles.length) throw new Error("This release has no role records.");
      var payloads = await Promise.all(index.roles.map(function (item) {
        return fetchJSON("/v1/roles/" + encodeURIComponent(item.id) + "?release=" + encodeURIComponent(index.release_id));
      }));
      payloads.forEach(function (item) {
        if (!item.role || item.role.schema !== "skills-vector.role/2" || item.role.release_id !== index.release_id || item.role.data_kind !== index.data_kind)
          throw new Error("Role records do not match the selected catalog release.");
      });
      buildViews(payloads);
      var saved = {};
      try { saved = JSON.parse(localStorage.getItem("sv-role-atlas") || "{}"); } catch (_) { /* storage is optional */ }
      if (!saved || typeof saved !== "object") saved = {};
      state.role = ROLES[saved.role] ? saved.role : Object.keys(ROLES)[0];
      state.level = role().levels.some(function (item) { return item.L === saved.level; }) ? saved.level : role().raw.skill_map.top_level_adds.core_level;
      state.tab = ["map", "levels", "industries", "gap", "agent"].includes(saved.tab) ? saved.tab : "map";
      state.mapLevel = role().levels.some(function (item) { return item.L === saved.mapLevel; }) ? saved.mapLevel : 0;
      state.mode = saved.mode === "conn" ? "conn" : "list";
      state.lens = saved.lens === "top" ? "top" : "level"; state.edges = saved.edges === "builds" ? "builds" : "co";
      state.industry = saved.industry || null; state.skill = null; state.node = null; state.have = defaultHave();
      renderAll();
      $("atlasContent").hidden = false; $("loadStatus").hidden = true; $("app").dataset.ready = "true";
    } catch (error) {
      $("fixtureCallout").hidden = true; $("app").dataset.ready = "error";
      $("loadTitle").textContent = "Role catalog unavailable";
      $("loadMessage").textContent = error.message + " Select a research catalog on the server, or explicitly enable its fixture mode. No fallback data is shown.";
      $("retryLoad").hidden = false;
    }
  }

  function persist() { try { localStorage.setItem("sv-role-atlas", JSON.stringify({ role: state.role, level: state.level, tab: state.tab, mapLevel: state.mapLevel, mode: state.mode, lens: state.lens, edges: state.edges, industry: state.industry })); } catch (e) {} }

  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }
  function role() { return ROLES[state.role]; }
  function level() { var r = role(); return r.levels.filter(function (l) { return l.L === state.level; })[0] || r.levels[0]; }

  function defaultHave() {
    // Example profile: everything up to one level below the target, minus one item.
    var r = role(), have = {}, skipped = false;
    r.levels.forEach(function (l) {
      if (l.L < state.level) l.exp.forEach(function (x) {
        if (!skipped && l.L === state.level - 1 && x[1] === "new") { skipped = true; return; }
        have[slug(x[0])] = true;
      });
    });
    return have;
  }

  function renderRolePick() {
    $("rolePick").innerHTML = Object.keys(ROLES).map(function (k) {
      return '<button type="button" data-role="' + k + '" aria-pressed="' + (k === state.role) + '">' + esc(ROLES[k].label) + (ROLES[k].publication === "experimental" ? ' <span class="xp">experimental</span>' : "") + "</button>";
    }).join("");
  }


  function renderLadder() {
    var r = role(), max = r.levels.length;
    $("stairs").innerHTML = r.levels.map(function (l, i) {
      var h = Math.round(38 + (62 * i) / Math.max(1, max - 1));
      return '<button type="button" class="step" data-level="' + l.L + '" aria-pressed="' + (l.L === state.level) + '" style="height:' + h + '%">' +
        '<span class="lv">L' + l.L + "</span><span class=\"nm\">" + esc(l.name) + '</span><span class="n">n=' + l.n + " · " + l.e + " emp</span></button>";
    }).join("");
    $("branch").innerHTML = r.branch ? '<span class="label">Linked role from L' + r.branch.from + ' · ' + esc(r.branch.label) + '</span><span class="muted" style="font-size:0.84rem">' + esc(r.branch.note) + "</span>" : '<p class="empty">No management link supplied.</p>';
  }

  function confidence(l) {
    if (l.e >= 15 && l.n >= 40) return ["high", "growing"];
    if (l.e >= 8) return ["medium", "established"];
    return ["low · thin evidence", "emerging"];
  }

  function renderSheet() {
    var r = role(), l = level(), c = confidence(l);
    var active = r.shifts.filter(function (s) { return s.levels.indexOf(l.L) > -1 && s.state !== "fading"; });
    $("sheet").innerHTML =
      '<div style="display:flex; flex-wrap:wrap; justify-content:space-between; gap:0.5rem; align-items:baseline">' +
        '<div><span class="label">Level ' + l.L + " of " + r.levels.length + '</span><h3 style="font-size:1.4rem">' + esc(l.name) + "</h3></div>" +
        '<span class="chip st-' + c[1] + '">evidence: ' + c[0] + "</span>" +
      "</div>" +
      '<dl class="kv">' +
        "<dt>Typical titles</dt><dd>" + (l.titles.map(esc).join(", ") || "Not established in this sample") + "</dd>" +
        "<dt>Scope</dt><dd>" + esc(l.scope) + "</dd>" +
        "<dt>Owns</dt><dd>" + esc(l.owns) + "</dd>" +
        '<dt>Years wording</dt><dd>"' + esc(l.years) + '" <span class="muted" style="font-size:0.8rem">as advertised, never used alone to level</span></dd>' +
        '<dt>Evidence</dt><dd class="num">' + l.n + " " + noun(l.n, "posting") + " · " + l.e + " " + noun(l.e, "employer") + " · " + r.window.current + "</dd>" +
      "</dl>" +
      '<div class="stepup"><span class="label">What changes from the level below</span><span>' + esc(l.stepUp) + "</span></div>" +
      '<div><div class="label" style="margin-bottom:0.3rem">Expectations at this level</div><ul class="exp">' +
        l.exp.map(function (x) {
          return "<li><span>" + esc(x[0]) + "</span>" + (x[1] === "new" ? '<span class="label tag-new">new at L' + l.L + "</span>" : '<span class="label tag-carry">' + (x[1] === "unknown" ? "introduction not established" : "carried over") + '</span>') + "</li>";
        }).join("") +
      "</ul>" + (!l.exp.length ? '<p class="empty">No assigned expectations in this sample.</p>' : "") + "</div>" +
      (active.length ? '<div><div class="label" style="margin-bottom:0.35rem">Shifts touching this level</div><div class="chips">' +
        active.map(function (s) { return '<span class="chip st-' + s.state + '">' + STATUS[s.status][0] + " · " + esc(s.label) + "</span>"; }).join("") + "</div></div>" : "");
  }


  function gapItems() {
    var r = role(), items = [], seen = {};
    r.levels.forEach(function (l) {
      if (l.L > state.level) return;
      l.exp.forEach(function (x) {
        var id = slug(x[0]);
        if (seen[id]) return;
        seen[id] = true;
        items.push({ id: id, label: x[0], L: l.L, kind: l.L === state.level ? "target" : "earlier", introduced: x[1] });
      });
    });
    r.shifts.forEach(function (s) {
      if (s.levels.indexOf(state.level) < 0 || s.state === "fading" || s.state === "contested") return;
      var id = "shift-" + s.id;
      if (seen[id]) return;
      seen[id] = true;
      items.push({ id: id, label: s.label, L: null, kind: "shift", state: s.state, status: s.status });
    });
    return items;
  }

  function renderGap() {
    var l = level(), items = gapItems();
    $("gapTarget").textContent = levelTitle(l.L);
    $("checks").innerHTML = items.map(function (it) {
      var tag = it.kind === "shift" ? '<span class="chip st-' + it.state + '">' + STATUS[it.status][0] + "</span>" : '<span class="lvl">L' + it.L + "</span>";
      return '<label><input type="checkbox" id="have-' + esc(it.id) + '" data-id="' + esc(it.id) + '"' + (state.have[it.id] ? " checked" : "") + "><span>" + esc(it.label) + "</span>" + tag + "</label>";
    }).join("") || '<p class="empty">No assigned expectations for this target in the sample. Unassigned postings are not promoted into this level.</p>';
    var rank = { target: 0, shift: 1, earlier: 2 };
    var missing = items.filter(function (it) { return !state.have[it.id]; }).sort(function (a, b) {
      if (rank[a.kind] !== rank[b.kind]) return rank[a.kind] - rank[b.kind];
      if (a.kind === "shift") return Number(b.state === "growing") - Number(a.state === "growing");
      return (b.L || 0) - (a.L || 0);
    });
    $("next").innerHTML = !items.length ? '<li class="empty">Insufficient assigned evidence for a gap comparison at this level.</li>' : missing.length ? missing.map(function (it) {
      var why = it.introduced === "unknown" ? "Observed in assigned L" + it.L + " postings; introduction and carry-over are not established." : it.kind === "target" ? "New at L" + state.level + ". This is what separates the target level from the one below."
        : it.kind === "shift" ? (it.state === "growing" ? "Rising signal that touches this level. Measured across two windows." : "Early signal. Not a confirmed trend, so lower priority.")
        : "Expected from L" + it.L + " and still assumed at the target level.";
      return "<li><div><div>" + esc(it.label) + '</div><div class="why">' + why + "</div></div></li>";
    }).join("") : '<li style="display:block" class="empty">Nothing missing for this level in the example profile. Pick a higher level to see the next step.</li>';
  }

  // ---- Skills graph ----
  function graphNodes() {
    return GRAPH[state.role].nodes.map(function (n) {
      return { id: n[0], short: n[1], label: n[2], from: n[3], row: n[4], state: n[5], sh: n[6], src: n[7], status: n[8], disputed: n[9] };
    });
  }
  function coEdges() { return GRAPH[state.role].co.filter(function (e) { return e[2] >= 1.5 && e[3] >= 25; }); }
  function topLevel() { var lv = role().levels; return lv[lv.length - 1].L; }
  function separatesTop(n) { var g = GRAPH[state.role], t = topLevel(); return comparableShares(n.sh[t - 1], n.sh[g.mid - 1]) && n.sh[t - 1] - n.sh[g.mid - 1] >= 20 && n.sh[t - 1] >= 40; }
  function levelName(L) { return role().levels.filter(function (l) { return l.L === L; })[0].name; }
  function levelTitle(L) { var name = levelName(L); return name === "L" + L ? name : "L" + L + " · " + name; }
  function missingComparison() { var g = GRAPH[state.role]; return !role().levels.find(function (l) { return l.L === g.mid; }).n || !role().levels.find(function (l) { return l.L === topLevel(); }).n; }
  function comparisonReason() {
    var assigned = role().levels.reduce(function (sum, l) { return sum + l.n; }, 0);
    return "Not enough level-assigned postings to compare levels: " + assigned + " of " + role().window.postings + " postings have a level.";
  }
  var SRC = { P: "Postings", O: "O*NET", Ee: "ESCO essential", Eo: "ESCO optional" };

  function renderGraphControls() {
    var r = role();
    $("graphRole").textContent = r.label;
    $("graphLevel").innerHTML = r.levels.map(function (l) {
      var on = state.lens === "level" && l.L === state.level;
      return '<button type="button" data-glevel="' + l.L + '" aria-pressed="' + on + '"' + (state.lens === "top" ? " disabled" : "") + ">L" + l.L + "</button>";
    }).join("");
    Array.prototype.forEach.call($("lensPick").children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.lens === state.lens)); });
    Array.prototype.forEach.call($("edgePick").children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.edges === state.edges)); });
  }

  function updateGraphScrollCue() {
    var graph = $("graph"), scrolls = graph.scrollWidth > graph.clientWidth;
    $("graphScrollCue").hidden = !scrolls;
    graph.tabIndex = scrolls ? 0 : -1;
    if (scrolls) graph.setAttribute("aria-describedby", "graphScrollCue");
    else graph.removeAttribute("aria-describedby");
  }

  function renderGraph() {
    renderGraphControls();
    var g = GRAPH[state.role], r = role(), nodes = graphNodes(), byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    var columns = r.levels.slice();
    if (nodes.some(function (n) { return n.from === null; })) columns.push({ L: null, name: "Unassigned" });
    var cols = columns.length, W = 960, left = 150, right = 10, colW = (W - left - right) / cols, topPad = 60, rowH = 62;
    var nRows = g.families.length ? g.families[g.families.length - 1][2] + 1 : 1, H = topPad + nRows * rowH + 6;
    var idx = (state.lens === "top" ? topLevel() : state.level) - 1;
    function px(n) { return left + colW * ((n.from === null ? cols : n.from) - 0.5); }
    function py(n) { return topPad + n.row * rowH + 20; }
    function rad(n) { var s = n.sh[idx]; return s > 0 ? 5 + Math.sqrt(s) : 4.5; }
    var sel = state.node && byId[state.node] ? state.node : null;
    var out = [];
    out.push('<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Skills graph for ' + esc(r.label) + ". " + nodes.length + " skills arranged by first observed assigned level, with unresolved assignments kept separate. " +
      (state.lens === "top" ? "Outlined skills separate L" + topLevel() + " from L" + g.mid + "." : "Dot size is the share of L" + state.level + " postings.") + '">');
    out.push('<defs><marker id="g-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="g-arrow" d="M0,0 L8,4 L0,8 z"/></marker></defs>');
    g.families.forEach(function (f, i) {
      var y = topPad + f[1] * rowH;
      if (i % 2 === 0) out.push('<rect class="g-band" x="0" y="' + y + '" width="' + W + '" height="' + (f[2] - f[1] + 1) * rowH + '"/>');
      out.push('<text class="g-fam" x="12" y="' + (y + 20) + '">' + esc(f[0]) + "</text>");
    });
    columns.forEach(function (l, i) {
      var cx = left + colW * (i + 0.5), on = l.L === idx + 1;
      out.push('<line class="g-col" x1="' + (left + colW * i) + '" y1="8" x2="' + (left + colW * i) + '" y2="' + H + '"/>');
      out.push('<text class="g-lv" x="' + cx + '" y="20" text-anchor="middle">' + (l.L === null ? "?" : "L" + l.L) + "</text>");
      out.push('<text class="g-lvname' + (on ? " on" : "") + '" x="' + cx + '" y="42" text-anchor="middle">' + esc(l.name) + "</text>");
    });
    if (state.edges === "co") {
      coEdges().forEach(function (e) {
        var a = byId[e[0]], b = byId[e[1]], x1 = px(a), y1 = py(a), x2 = px(b), y2 = py(b);
        var mx = (x1 + x2) / 2, my = (y1 + y2) / 2, dx = x2 - x1, dy = y2 - y1, len = Math.sqrt(dx * dx + dy * dy) || 1;
        var bend = x1 === x2 ? 34 : Math.max(16, Math.min(60, len * 0.14)), cx = mx - (dy / len) * bend, cy = my + (dx / len) * bend;
        var cls = sel ? (e[0] === sel || e[1] === sel ? " on" : " off") : "";
        out.push('<path class="g-edge' + cls + '" stroke-width="' + (1 + (e[2] - 1) * 2).toFixed(2) + '" d="M' + x1 + "," + y1 + " Q" + cx.toFixed(1) + "," + cy.toFixed(1) + " " + x2 + "," + y2 + '"/>');
      });
    } else {
      g.builds.forEach(function (e) {
        var a = byId[e[0]], b = byId[e[1]], x1 = px(a), y1 = py(a), x2 = px(b), y2 = py(b);
        var dx = x2 - x1, dy = y2 - y1, len = Math.sqrt(dx * dx + dy * dy) || 1, ra = rad(a) + 3, rb = rad(b) + 5;
        var cls = sel ? (e[0] === sel || e[1] === sel ? " on" : " off") : "";
        out.push('<line class="g-edge build' + cls + '" x1="' + (x1 + dx / len * ra).toFixed(1) + '" y1="' + (y1 + dy / len * ra).toFixed(1) + '" x2="' + (x2 - dx / len * rb).toFixed(1) + '" y2="' + (y2 - dy / len * rb).toFixed(1) + '" marker-end="url(#g-arrow)"/>');
      });
    }
    nodes.forEach(function (n) {
      var x = px(n), y = py(n), s = n.sh[idx], rr = rad(n), top = state.lens === "top", diff = separatesTop(n);
      var lvl = idx + 1, tip = n.label + ". " + levelLabel(n.from) + ". " + STATUS[n.status][0] + ": " + (s === null ? "no assigned sample at L" + lvl : s + "% of L" + lvl + " postings") + (top && diff ? ". Separates the top level." : "") + (n.disputed ? ". Disputed: " + n.disputed : "");
      out.push('<g class="g-n' + (top && !diff ? " dim" : "") + '" data-node="' + esc(n.id) + '" tabindex="0" role="button" aria-pressed="' + (sel === n.id) + '" aria-label="' + esc(tip) + '">');
      out.push("<title>" + esc(tip) + "</title>");
      // A 60px hit box stays 44px at the SVG's 704px minimum. Offset it to include the label without overlapping the next row.
      out.push('<rect class="g-hit" x="' + (x - 30) + '" y="' + (y - 26) + '" width="60" height="60"/>');
      out.push('<circle class="g-sel" cx="' + x + '" cy="' + y + '" r="' + (rr + 3).toFixed(1) + '"/>');
      if (top && diff) out.push('<circle class="g-ring" cx="' + x + '" cy="' + y + '" r="' + (rr + 4.5).toFixed(1) + '"/>');
      if (s === null) out.push('<text class="g-unknown" x="' + x + '" y="' + (y + 4) + '">?</text>');
      else out.push('<circle class="g-dot st-' + n.state + (s > 0 ? "" : " hollow") + '" cx="' + x + '" cy="' + y + '" r="' + rr.toFixed(1) + '"/>');
      if (n.disputed) out.push('<text class="g-flag" aria-hidden="true" x="' + (x + rr + 5) + '" y="' + (y - rr) + '">!</text>');
      out.push('<text class="g-label" x="' + x + '" y="' + (y + 33) + '" text-anchor="middle">' + esc(n.short) + "</text>");
      out.push("</g>");
    });
    out.push("</svg>");
    $("graph").innerHTML = out.join("");
    updateGraphScrollCue();
    renderGraphPanel(nodes, byId);
  }

  function shareList(items, idx) {
    return '<ul class="pairs">' + items.map(function (n) { return "<li><span>" + esc(n.label) + '</span><span class="mono">' + n.sh[idx] + "%</span></li>"; }).join("") + "</ul>";
  }

  function renderGraphPanel(nodes, byId) {
    var g = GRAPH[state.role], t = topLevel(), html;
    var n = state.node && byId[state.node];
    if (n) {
      var lvl = state.lens === "top" ? t : state.level;
      var pairs = coEdges().filter(function (e) { return e[0] === n.id || e[1] === n.id; }).sort(function (a, b) { return b[2] - a[2]; });
      var on = g.builds.filter(function (e) { return e[1] === n.id; }).map(function (e) { return byId[e[0]].short; });
      var leads = g.builds.filter(function (e) { return e[0] === n.id; }).map(function (e) { return byId[e[1]].short; });
      html = '<div style="display:grid; gap:0.45rem"><div class="label">Skill</div><h3>' + esc(n.label) + "</h3>" +
        '<div class="chips"><span class="chip st-' + n.state + '">' + STATUS[n.status][0] + '</span><span class="chip src">' + levelLabel(n.from) + "</span>" +
        (separatesTop(n) ? '<span class="chip analysis">separates L' + t + "</span>" : "") + "</div>" +
        (n.disputed ? '<p class="caveat"><strong>Disputed.</strong> ' + esc(n.disputed) + "</p>" : "") +
        '<div class="chips">' + n.src.map(function (c) { return '<span class="chip src">' + SRC[c] + "</span>"; }).join("") + "</div></div>" +
        '<div style="display:grid; gap:0.4rem"><div class="label">Share of postings by level</div><div class="bars">' +
        role().levels.map(function (l) {
          var v = n.sh[l.L - 1];
          return '<div class="win" style="color:var(' + (l.L === lvl ? "--present" : "--ink-mid") + ')"><span>L' + l.L + '</span><span class="track"><span class="bar cur" style="width:' + (v === null ? 0 : v) + '%"></span></span><span>' + shareLabel(v) + "</span></div>";
        }).join("") + "</div></div>" +
        '<div style="display:grid; gap:0.3rem"><div class="label">Most often asked together with</div>' +
        (pairs.length ? '<ul class="pairs">' + pairs.map(function (e) {
          var o = byId[e[0] === n.id ? e[1] : e[0]];
          return "<li><span>" + esc(o.label) + '</span><span class="mono">' + e[2].toFixed(1) + "× · " + e[3] + " postings</span></li>";
        }).join("") + "</ul>" : '<p class="empty">No pair above the threshold.</p>') + "</div>" +
        (on.length || leads.length ? '<dl class="kv">' + (on.length ? "<dt>Builds on</dt><dd>" + esc(on.join(", ")) + "</dd>" : "") + (leads.length ? "<dt>Leads to</dt><dd>" + esc(leads.join(", ")) + "</dd>" : "") + "</dl>" : "") +
        '<button type="button" class="linkbtn" id="clearNode">Back to the summary</button>';
    } else if (state.lens === "top" && missingComparison()) {
      html = '<div class="label">Top level adds</div><p class="empty">' + esc(comparisonReason()) + "</p>";
    } else if (state.lens === "top") {
      var mid = g.mid, sep = nodes.filter(separatesTop).sort(function (a, b) { return (b.sh[t - 1] - b.sh[mid - 1]) - (a.sh[t - 1] - a.sh[mid - 1]); });
      var drop = nodes.filter(function (x) { return comparableShares(x.sh[mid - 1], x.sh[t - 1]) && x.sh[mid - 1] - x.sh[t - 1] >= 15; });
      var carry = nodes.filter(function (x) { return !separatesTop(x) && drop.indexOf(x) < 0 && x.sh[t - 1] >= 30 && x.sh[mid - 1] >= 30; });
      var missingShares = nodes.some(function (x) { return !comparableShares(x.sh[t - 1], x.sh[mid - 1]); });
      html = (missingShares ? '<p class="empty">No measured comparison for some skills: missing assigned level samples are not zero demand.</p>' : "") + '<div style="display:grid; gap:0.35rem"><div class="label">Top level adds</div><h3>What L' + t + " · " + esc(levelName(t)) + " asks for that L" + mid + " · " + esc(levelName(mid)) + " does not</h3></div>" +
        '<div style="display:grid; gap:0.3rem"><div class="label">Separates the top · share at L' + t + " vs L" + mid + '</div><ul class="pairs">' +
        sep.map(function (x) { return "<li><span>" + esc(x.label) + '</span><span class="mono">' + x.sh[t - 1] + "% vs " + x.sh[mid - 1] + "%</span></li>"; }).join("") + "</ul></div>" +
        '<div style="display:grid; gap:0.3rem"><div class="label">Carried from L' + mid + '</div><ul class="pairs">' +
        carry.map(function (x) { return "<li><span>" + esc(x.label) + '</span><span class="mono">' + x.sh[t - 1] + "%</span></li>"; }).join("") + "</ul></div>" +
        (drop.length ? '<div style="display:grid; gap:0.3rem"><div class="label">Asked less at the top</div><ul class="pairs">' +
          drop.map(function (x) { return "<li><span>" + esc(x.label) + '</span><span class="mono">' + x.sh[mid - 1] + "% → " + x.sh[t - 1] + "%</span></li>"; }).join("") + "</ul></div>" : "") +
        '<p class="caveat">This shows what employers ask of the top level, not what makes someone excellent at it. Saying more needs practitioner review and O*NET level ratings.</p>';
    } else {
      var i = state.level - 1, most = nodes.filter(function (x) { return x.sh[i] > 0; }).sort(function (a, b) { return b.sh[i] - a.sh[i]; }).slice(0, 6);
      html = '<div style="display:grid; gap:0.35rem"><div class="label">Most asked at this level</div><h3>' + esc(levelTitle(state.level)) + "</h3></div>" +
        (most.length ? shareList(most, i) : '<p class="empty">No measured skill shares at this level. A missing assigned sample is not zero demand.</p>') +
        '<p class="muted" style="font-size:0.84rem">Select a skill in the graph to see its share at every level, what it is asked together with, and what it builds on.</p>';
    }
    $("graphPanel").innerHTML = html;
  }

  // ---- Industry view ----
  function industries() { return INDUSTRY[state.role].list; }
  function industry() {
    var list = industries();
    return list.filter(function (x) { return x.key === state.industry; })[0] || list[0];
  }

  function renderIndustry() {
    var r = role(), list = industries(), ind = industry(), byId = {};
    if (!list.length) { $("industryPick").innerHTML = ""; $("indSheet").innerHTML = '<p class="empty">No sector profiles supplied in this release.</p>'; $("heat").innerHTML = ""; return; }
    graphNodes().forEach(function (n) { byId[n.id] = n; });
    state.industry = ind.key;
    $("indRole").textContent = r.label;
    $("industrySourceNote").textContent = r.raw.data_kind === "fixture"
      ? "Duties come from each sector\'s postings, certifications from CareerOneStop, and the share of jobs from the BLS occupation-by-industry matrix."
      : "Only verified employer-sector mappings contribute. Other employers are excluded, not assigned from customer industry. Official industry shares and credential catalogs are not assumed.";
    $("industryPick").innerHTML = list.map(function (x) {
      return '<button type="button" data-ind="' + x.key + '" aria-pressed="' + (x.key === ind.key) + '">' + esc(x.label) + "</button>";
    }).join("");
    if (ind.thin) {
      $("indSheet").innerHTML = '<div style="display:grid; gap:0.35rem"><div class="label">Sector</div><h3 style="font-size:1.35rem">' + esc(ind.label) + '</h3><div class="mono muted">NAICS ' + esc(ind.naics) + "</div></div>" +
        '<p class="caveat">Evidence: ' + ind.postings + " " + noun(ind.postings, "posting") + " from " + ind.employers + " " + noun(ind.employers, "employer") + " in " + r.window.current + ". Profile not published: " + esc(ind.reason) + ". A profile needs 30+ postings and 8+ employers. The gap stays visible.</p>";
    } else
    $("indSheet").innerHTML =
      '<div style="display:grid; gap:0.35rem"><div class="label">Sector</div><h3 style="font-size:1.35rem">' + esc(ind.label) + '</h3><div class="mono muted">NAICS ' + esc(ind.naics) + " · " + esc(ind.naicsTitle) + '</div><div class="muted" style="font-size:0.84rem">Largest in sample: ' + esc(ind.sub) + "</div></div>" +
      '<div class="found">' +
        '<div><span class="v">' + esc(ind.share) + '</span><span class="label">of this job\'s U.S. employment (if supplied)</span></div>' +
        '<div><span class="v num">' + ind.postings + '</span><span class="label">' + noun(ind.postings, 'posting') + ' · ' + ind.employers + ' ' + noun(ind.employers, 'employer') + '</span></div>' +
        '<div><span class="v">' + esc(ind.pay) + '</span><span class="label">median pay in this industry</span></div>' +
      "</div>" +
      '<div class="stepup"><span class="label">How it differs here</span><span>' + esc(ind.differs) + "</span></div>" +
      '<div style="display:grid; gap:0.3rem"><div class="label">Duties specific to this industry</div><ul class="pairs">' +
        ind.duties.map(function (d) { return "<li><span>" + esc(d) + "</span><span></span></li>"; }).join("") + "</ul></div>" +
      '<div style="display:grid; gap:0.3rem"><div class="label">Certifications and licences</div>' +
        (ind.certs.length ? '<div class="certs"><table><thead><tr><th scope="col">Credential</th><th scope="col">Issuer</th><th scope="col">Asked as</th><th scope="col" style="text-align:right">Postings</th></tr></thead><tbody>' +
          ind.certs.map(function (c) { return "<tr><td>" + esc(c[0]) + '</td><td class="muted">' + esc(c[1]) + '</td><td><span class="req req-' + c[2] + '">' + c[2] + '</span></td><td class="n">' + c[3] + "%</td></tr>"; }).join("") +
          "</tbody></table></div>" : '<p class="empty">No certifications supplied in this sector profile.</p>') +
        '<span class="rule-note">' + (ind.sources.some(function (source) { return source.source_kind === "careeronestop"; })
          ? "Matched in CareerOneStop Certification Finder by O*NET code and NAICS. The share is postings that name the credential."
          : "Credentials are literal published posting mentions. No official issuer or credential catalog is inferred.") + '</span></div>' +
      '<div style="display:grid; gap:0.3rem"><div class="label">Domain knowledge</div><div class="know">' + ind.know.map(function (k) { return "<span>" + esc(k) + "</span>"; }).join("") + "</div></div>";

    var col = list.map(function (x) { return x.key; }).indexOf(ind.key);
    $("heat").innerHTML = '<table><thead><tr><th scope="col" style="text-align:left">Skill</th>' +
      list.map(function (x, i) { return '<th scope="col" class="' + (i === col ? "on" : "") + '"><button type="button" class="linkbtn" data-ind="' + x.key + '" style="color:inherit">' + esc(x.label) + "</button></th>"; }).join("") +
      "</tr></thead><tbody>" +
      INDUSTRY[state.role].heat.map(function (row) {
        return '<tr><th scope="row">' + esc(byId[row[0]].short) + "</th>" + row[1].map(function (v, i) {
          if (v === null) return '<td class="' + (i === col ? "on" : "") + '" title="Below the sector floor">–</td>';
          var mix = Math.min(88, Math.round(v * 1.15));
          return '<td class="' + (mix > 48 ? "hi " : "") + (i === col ? "on" : "") + '" style="background:color-mix(in srgb, var(--present) ' + mix + '%, var(--surface))" title="' + esc(byId[row[0]].label + " · " + list[i].label + ": " + v + "% of postings") + '">' + v + "%</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody></table>";
  }

  // ---- Skill map ----
  function neededAt(k, L) { return k.sh[L - 1] >= 15; }

  function renderTopLine(list) {
    var g = GRAPH[state.role], t = topLevel();
    if (missingComparison()) {
      $("topLine").innerHTML = '<span class="label">Top level adds</span><span>' + esc(comparisonReason()) + "</span>";
      return;
    }
    var sep = list.filter(function (k) { return comparableShares(k.sh[t - 1], k.sh[g.mid - 1]) && k.sh[t - 1] - k.sh[g.mid - 1] >= 20 && k.sh[t - 1] >= 40; })
      .sort(function (a, b) { return (b.sh[t - 1] - b.sh[g.mid - 1]) - (a.sh[t - 1] - a.sh[g.mid - 1]); });
    $("topLine").innerHTML = '<span class="label">What the top level adds</span><span>Compared with L' + g.mid + " · " + esc(levelName(g.mid)) + ", postings for L" + t + " · " + esc(levelName(t)) + " add: " +
      (sep.map(function (k) { return "<strong>" + esc(k.short) + "</strong> (" + k.sh[t - 1] + "% vs " + k.sh[g.mid - 1] + "%)"; }).join(", ") || "no measured additions; missing level samples are not zero demand") +
      ". " + esc(role().raw.skill_map.top_level_adds.long_run_bar.reading) + "</span>";
  }

  function renderMap() {
    var r = role(), list = skills(), L = state.mapLevel;
    if (L && !r.levels.some(function (l) { return l.L === L; })) { L = state.mapLevel = 0; }
    $("mapLevel").innerHTML = '<button type="button" data-mlevel="0" aria-pressed="' + (L === 0) + '">All</button>' +
      r.levels.map(function (l) { return '<button type="button" data-mlevel="' + l.L + '" aria-pressed="' + (L === l.L) + '">L' + l.L + "</button>"; }).join("");
    Array.prototype.forEach.call($("mapMode").children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)); });
    $("mapList").hidden = state.mode !== "list"; $("connWrap").hidden = state.mode !== "conn";
    renderTopLine(list);
    var shown = list.filter(function (k) { return !L || neededAt(k, L) || (k.status === "fading" && k.sh[L - 1] > 0); });
    if (state.skill && !shown.some(function (k) { return k.id === state.skill; })) state.skill = null;
    if (!state.skill && shown.length) state.skill = (shown.filter(function (k) { return k.status === "rising"; })[0] || shown[0]).id;
    $("map").innerHTML = ["core", "rising", "early", "fading"].map(function (st) {
      var rows = shown.filter(function (k) { return k.status === st; });
      if (!rows.length) return "";
      if (st === "core") rows.sort(function (a, b) { return a.from - b.from; });
      return '<div class="lane"><div class="lane-head"><span class="chip st-' + STATUS[st][1] + '">' + STATUS[st][0] + '</span><span class="muted">' + STATUS[st][2] + '</span><span class="mono muted" style="margin-left:auto">' + rows.length + "</span></div>" +
        '<div class="cols" aria-hidden="true"><span>Skill</span><span>Levels</span><span>Evidence</span><span>Forecast</span></div>' +
        rows.map(function (k) {
          var pips = r.levels.map(function (l) { return '<i class="' + (neededAt(k, l.L) ? "on" : "") + '">' + (k.sh[l.L - 1] === null ? "?" : l.L) + "</i>"; }).join("");
          var ev = ["P", "O", "M"].map(function (f) { return '<i class="' + (k.fams[f] ? "on" : "") + '">' + f + "</i>"; }).join("");
          var call = k.call ? (k.call.basis === "none" ? "No forecast yet" : "<strong>" + percentage(k.call.probability) + "%</strong> · " + esc(k.call.resolves)) : "–";
          var lvls = r.levels.filter(function (l) { return neededAt(k, l.L); }).map(function (l) { return "L" + l.L; }).join(", ") || (k.sh.some(function (v) { return v === null; }) ? "no measured assigned coverage" : "none above 15%");
          var evTxt = ["P", "O", "M"].filter(function (f) { return k.fams[f]; }).map(function (f) { return { P: "postings", O: "official map", M: "market trend" }[f]; }).join(", ");
          return '<button type="button" class="srow" data-skill="' + esc(k.id) + '" aria-pressed="' + (state.skill === k.id) + '" aria-label="' + esc(k.label + ". " + STATUS[st][0] + ". Needed at " + lvls + ". Evidence: " + evTxt + "." + (k.disputed ? " Disputed: " + k.disputed + "." : "") + (k.call ? k.call.basis === "none" ? " No forecast yet." : " Forecast " + percentage(k.call.probability) + "%: " + k.call.claim + "." : "")) + '">' +
            "<span>" + esc(k.label) + (k.disputed ? '<span class="flag">disputed</span>' : "") + (!k.official && st === "core" ? '<span class="flag gap">not in official maps yet</span>' : "") + "</span>" +
            '<span class="pips" aria-hidden="true">' + pips + '</span><span class="pips ev" aria-hidden="true">' + ev + '</span><span class="call" aria-hidden="true">' + call + "</span></button>";
        }).join("") + "</div>";
    }).join("") + '<p class="rule-note">Levels: filled where 15%+ of that level\'s postings ask for it. ? means no assigned sample. Evidence: P postings · O official map (O*NET or ESCO) · M market trend (Hiring Lab, Hot Technologies, Economic Index).</p>';
    renderSkillPanel(list);
  }





  function highlight(json) {
    return esc(json).replace(/(&quot;[^&]*?(?:&(?!quot;)[^&]*?)*&quot;)(\s*:)?/g, function (m, str, colon) {
      return colon ? '<span class="k">' + str + "</span>" + colon : '<span class="s">' + str + "</span>";
    });
  }

  function renderAgent() {
    var text = JSON.stringify(recordJSON(), null, 2);
    $("json").innerHTML = highlight(text);
    $("json").dataset.raw = text;
    $("agentPath").textContent = "GET /v1/roles/" + state.role + "?release=" + encodeURIComponent(role().raw.release_id);
    $("shiftsEndpoint").textContent = "GET /v1/shifts?since=" + encodeURIComponent(role().raw.demand_window.compared_to || role().raw.demand_window.id);
  }

  function renderAll() { renderRolePick(); renderHead(); renderTabs(); renderMap(); renderLadder(); renderSheet(); renderGraph(); renderIndustry(); renderGap(); renderAgent(); renderMethod(); persist(); }

  $("rolePick").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-role]"); if (!b) return;
    state.role = b.dataset.role;
    var r = role(); state.level = r.levels[Math.min(2, r.levels.length - 1)].L;
    state.have = defaultHave(); state.node = null; state.industry = null; state.skill = null; state.mapLevel = 0; renderAll();
  });
  $("stairs").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-level]"); if (!b) return;
    state.level = Number(b.dataset.level); state.have = defaultHave();
    renderLadder(); renderSheet(); renderGraph(); renderGap(); renderMethod(); persist();
  });
  $("graphLevel").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-glevel]"); if (!b) return;
    state.level = Number(b.dataset.glevel); state.have = defaultHave();
    renderLadder(); renderSheet(); renderGraph(); renderGap(); renderMethod(); persist();
  });
  $("lensPick").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-lens]"); if (!b) return;
    state.lens = b.dataset.lens; renderGraph(); persist();
  });
  $("edgePick").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-edges]"); if (!b) return;
    state.edges = b.dataset.edges; renderGraph(); persist();
  });
  function pickNode(el) {
    var id = el.dataset.node;
    state.node = state.node === id ? null : id; renderGraph();
    var again = findData("#graph", "node", id); if (again) again.focus();
  }
  $("graph").addEventListener("click", function (e) { var n = e.target.closest("[data-node]"); if (n) pickNode(n); });
  $("graph").addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var n = e.target.closest("[data-node]"); if (!n) return;
    e.preventDefault(); pickNode(n);
  });
  $("graphPanel").addEventListener("click", function (e) {
    if (e.target.id !== "clearNode") return;
    var previous = state.node;
    state.node = null; renderGraph();
    var node = findData("#graph", "node", previous); if (node) node.focus();
  });
  function pickIndustry(e) {
    var b = e.target.closest("button[data-ind]"); if (!b) return;
    state.industry = b.dataset.ind; renderIndustry(); renderAgent(); persist();
  }
  $("industryPick").addEventListener("click", pickIndustry);
  $("heat").addEventListener("click", pickIndustry);
  $("tabs").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-tab]"); if (!b) return;
    state.tab = b.dataset.tab; renderTabs(); persist();
  });
  $("tabs").addEventListener("keydown", function (e) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    if (!e.target.matches('[role="tab"]')) return;
    var bs = Array.from($("tabs").children), i = bs.indexOf(e.target);
    i = e.key === "Home" ? 0 : e.key === "End" ? bs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : bs.length - 1)) % bs.length;
    e.preventDefault(); state.tab = bs[i].dataset.tab; renderTabs(); bs[i].focus(); persist();
  });
  $("mapLevel").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-mlevel]"); if (!b) return;
    state.mapLevel = Number(b.dataset.mlevel); renderMap(); persist();
  });
  $("mapMode").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-mode]"); if (!b) return;
    state.mode = b.dataset.mode; renderMap(); updateGraphScrollCue(); persist();
  });
  $("map").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-skill]"); if (!b) return;
    state.skill = b.dataset.skill; renderMap();
    var again = findData("#map", "skill", state.skill); if (again) again.focus();
  });
  $("checks").addEventListener("change", function (e) {
    var id = e.target.dataset && e.target.dataset.id; if (!id) return;
    state.have[id] = e.target.checked; renderGap();
    var el = $("have-" + id); if (el) el.focus();
  });
  $("copyJson").addEventListener("click", function () {
    var btn = this, raw = $("json").dataset.raw;
    function selectIt() { var rng = document.createRange(); rng.selectNodeContents($("json")); var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(rng); btn.textContent = "Selected. Press Ctrl+C"; }
    try {
      navigator.clipboard.writeText(raw).then(function () { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy JSON"; }, 1600); }, selectIt);
    } catch (err) { selectIt(); }
  });

  new ResizeObserver(updateGraphScrollCue).observe($("graph"));
  $("retryLoad").addEventListener("click", loadCatalog);
  document.addEventListener("click", function (event) {
    var button = event.target.closest("button");
    if (!button) return;
    var key = ["role", "level", "glevel", "mlevel", "ind", "skill"].find(function (name) { return name in button.dataset; });
    var root = button.parentElement.closest("[id]");
    if (!key || !root) return;
    var value = button.dataset[key];
    queueMicrotask(function () { var next = findData("#" + root.id, key, value); if (next) next.focus({ preventScroll: true }); });
  }, true);
  loadCatalog();
})();
