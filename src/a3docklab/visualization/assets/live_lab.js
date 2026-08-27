(function () {
  const live = { sessionId: null, token: null, running: false, busy: false, timer: null, events: [], lastStep: -1, droppedFrames: 0, reconnects: 0 };
  const byId = (id) => document.getElementById(id);
  const value = (id) => {
    const host = byId(id);
    if (host && host.value !== undefined) return host.value;
    const input = host && host.querySelector("input");
    return input ? input.value : null;
  };
  const setText = (id, text) => { const node = byId(id); if (node) node.textContent = text; };

  async function api(path, options) {
    const headers = {"Content-Type": "application/json", ...(options.headers || {})};
    if (live.token) headers["X-A3DockLab-Control-Token"] = live.token;
    const response = await fetch(path, {...options, headers});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }

  async function reportClientMetrics() {
    if (!live.droppedFrames && !live.reconnects) return;
    const payload = {dropped_frames: live.droppedFrames, reconnects: live.reconnects};
    live.droppedFrames = 0; live.reconnects = 0;
    try {
      await fetch("/api/operations/client-metrics", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
      });
    } catch (_) {
      live.droppedFrames += payload.dropped_frames; live.reconnects += payload.reconnects;
    }
  }

  function requestId() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function dropdownRawValue(id) {
    const host = byId(id);
    if (host && host.value !== undefined) return host.value;
    const reactKey = host && Object.keys(host).find((key) => key.startsWith("__reactProps"));
    const props = reactKey && host[reactKey];
    if (props && props.children && props.children.props) return props.children.props.value;
    const input = host && host.querySelector("input");
    return input && input.value;
  }

  function intent() {
    if (dropdownRawValue("live-active-policy")) return null;
    const mode = dropdownRawValue("live-mode") || "autopilot";
    const result = {mode};
    if (mode === "velocity") result.desired_velocity_m_s = ["x", "y", "z"].map((axis) => +(value(`live-v${axis}`) || 0));
    result.desired_torque_n_m = ["x", "y", "z"].map((axis) => +(value(`live-t${axis}`) || 0));
    return result;
  }

  function graph(id) {
    const host = byId(id);
    return host && host.querySelector(".js-plotly-plot");
  }

  function render(payload, reset) {
    const twin = graph("live-twin"), history = graph("live-history");
    if (reset && twin) Plotly.restyle(twin, {x: [[]], y: [[]], z: [[]]}, [0]);
    if (reset && history) Plotly.restyle(history, {x: [[], []], y: [[], []]}, [0, 1]);
    if (!payload || !payload.frame) return;
    const state = payload.frame.state;
    const newFrame = payload.step_index !== live.lastStep;
    if (twin) {
      if (newFrame) Plotly.extendTraces(twin, {x: [[state.x_m]], y: [[state.y_m]], z: [[state.z_m]]}, [0], 4000);
      Plotly.restyle(twin, {x: [[[state.x_m]]], y: [[[state.y_m]]], z: [[[state.z_m]]], "marker.color": [[state.warning ? "#ff496c" : "#5ee7ff"]]}, [1]);
    }
    if (history) {
      if (newFrame) Plotly.extendTraces(history, {x: [[state.time_s], [state.time_s]], y: [[state.range_m], [state.closing_rate_m_s]]}, [0, 1], 4000);
    }
    const ttc = state.closing_rate_m_s > 1e-6 ? state.range_m / state.closing_rate_m_s : Infinity;
    setText("live-kpis", `${payload.scenario_id} · ${payload.lifecycle.toUpperCase()} · ${state.time_s.toFixed(1)} s · ${state.range_m.toFixed(2)} m range · ${state.closing_rate_m_s.toFixed(3)} m/s closing · ${state.phase} · TTC ${Number.isFinite(ttc) ? ttc.toFixed(1) + " s" : "—"} · propellant ${state.propellant_used_kg.toFixed(2)} kg`);
    const decision = payload.frame.decision;
    setText("live-decision", decision ? `${decision.status.toUpperCase()}: ${decision.requested_mode}\n${decision.reason}\nrequested ${JSON.stringify(decision.requested_velocity_m_s)}\nexecuted  ${JSON.stringify(decision.executed_velocity_m_s)}` : "Reference autopilot");
    const activeEvaluation = payload.frame.active_policy_evaluation;
    setText("live-policy-health", activeEvaluation ? `${activeEvaluation.policy.policy_id} ${activeEvaluation.policy.policy_version}\n${activeEvaluation.health.toUpperCase()} · ${activeEvaluation.latency_ms.toFixed(2)} / ${activeEvaluation.latency_budget_ms.toFixed(2)} ms\n${activeEvaluation.detail}\nfallback ${activeEvaluation.fallback_applied ? activeEvaluation.requested_intent.mode : "not used"}\nartifact ${activeEvaluation.policy.artifact_uri}\nrevision ${activeEvaluation.policy.code_revision}` : "Human operator active");
    const shadow = payload.frame.shadow_decision;
    const shadowEvaluation = payload.frame.shadow_policy_evaluation;
    setText("live-shadow", shadow ? `${payload.frame.shadow_policy.policy_id} ${payload.frame.shadow_policy.policy_version}\n${shadowEvaluation.health.toUpperCase()} · ${shadowEvaluation.latency_ms.toFixed(2)} ms\n${shadow.status.toUpperCase()}: ${shadow.requested_mode}\n${shadow.reason}\nproposed ${JSON.stringify(shadow.requested_velocity_m_s)}\napproved ${JSON.stringify(shadow.executed_velocity_m_s)}\nSHADOW ONLY — no control authority` : "Disabled");
    if (payload.frame.events && payload.frame.events.length) live.events.push(...payload.frame.events);
    setText("live-events", live.events.slice(-12).map((event) => `${event.time_s.toFixed(1)}s · ${event.event_type} · ${event.detail}`).join("\n") || "No events yet");
    if (["complete", "terminated"].includes(payload.lifecycle)) stop();
    live.lastStep = payload.step_index;
  }

  async function control(action, withIntent) {
    if (!live.sessionId || live.busy) return;
    live.busy = true;
    try {
      const payload = await api(`/api/simulations/${live.sessionId}/control`, {method: "POST", headers: {"Idempotency-Key": requestId()}, body: JSON.stringify({action, ...(withIntent ? {intent: intent()} : {})})});
      render(payload, action === "reset");
      setText("live-lease", `Control lease: ${payload.owner} · ${payload.lifecycle}`);
      return payload;
    } catch (error) {
      setText("live-lease", `Control error: ${error.message}`);
      stop();
    } finally { live.busy = false; }
  }

  function stop() {
    live.running = false;
    if (live.timer) clearInterval(live.timer);
    live.timer = null;
  }

  function runLoop() {
    stop();
    live.running = true;
    const rate = +(dropdownRawValue("live-rate") || 10);
    live.timer = setInterval(async () => {
      if (!live.running) return;
      if (live.busy) { live.droppedFrames += 1; await reportClientMetrics(); return; }
      const result = await control("advance", true);
      if (result && result.lifecycle !== "running") stop();
    }, Math.max(40, 1000 / rate));
  }

  function bind(id, handler) {
    const node = byId(id);
    if (node && !node.dataset.liveBound) { node.addEventListener("click", handler); node.dataset.liveBound = "1"; }
  }

  function attach() {
    bind("live-create", async () => {
      stop(); live.events = []; live.lastStep = -1;
      try {
        const scenario = dropdownRawValue("live-scenario");
        const fault = dropdownRawValue("live-fault") || "none";
        const active_policy_id = dropdownRawValue("live-active-policy") || null;
        const shadow_policy_id = dropdownRawValue("live-shadow-policy") || null;
        const latency_budget_ms = +(value("live-policy-budget") || 50);
        const fallback_mode = dropdownRawValue("live-policy-fallback") || "hold";
        const payload = await api("/api/simulations", {method: "POST", body: JSON.stringify({scenario_id: scenario, fault, active_policy_id, shadow_policy_id, latency_budget_ms, fallback_mode})});
        live.sessionId = payload.session_id; live.token = payload.control_token;
        setText("live-lease", `Control lease: ${payload.owner} · ${payload.session_id.slice(0, 8)}`);
        await control("step", true);
      } catch (error) { setText("live-lease", `Create failed: ${error.message}`); }
    });
    bind("live-resume", async () => { const result = await control("resume", false); if (result) runLoop(); });
    bind("live-pause", async () => { stop(); await control("pause", false); });
    bind("live-step", async () => { stop(); await control("step", true); });
    bind("live-reset", async () => { stop(); live.events = []; live.lastStep = -1; await control("reset", false); });
    bind("live-terminate", async () => { stop(); await control("terminate", false); });
  }

  new MutationObserver(attach).observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener("DOMContentLoaded", attach);
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible" || !live.sessionId) return;
    try {
      const payload = await api(`/api/simulations/${live.sessionId}`, {method: "GET", headers: {}});
      live.reconnects += 1; render(payload, false); await reportClientMetrics();
    } catch (_) { /* the lease UI reports the next control failure */ }
  });
})();
