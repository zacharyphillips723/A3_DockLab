(function () {
  const live = { sessionId: null, token: null, running: false, busy: false, timer: null, events: [], lastStep: -1 };
  const byId = (id) => document.getElementById(id);
  const value = (id) => {
    const host = byId(id);
    if (host && host.value !== undefined) return host.value;
    const input = host && host.querySelector("input");
    return input ? input.value : null;
  };
  const setText = (id, text) => { const node = byId(id); if (node) node.textContent = text; };

  async function api(path, options) {
    const headers = {"Content-Type": "application/json"};
    if (live.token) headers.Authorization = `Bearer ${live.token}`;
    const response = await fetch(path, {...options, headers});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
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
    const shadow = payload.frame.shadow_decision;
    setText("live-shadow", shadow ? `${payload.frame.shadow_policy.policy_id} ${payload.frame.shadow_policy.policy_version}\n${shadow.status.toUpperCase()}: ${shadow.requested_mode}\n${shadow.reason}\nproposed ${JSON.stringify(shadow.requested_velocity_m_s)}\napproved ${JSON.stringify(shadow.executed_velocity_m_s)}\nSHADOW ONLY — no control authority` : "Disabled");
    if (payload.frame.events && payload.frame.events.length) live.events.push(...payload.frame.events);
    setText("live-events", live.events.slice(-12).map((event) => `${event.time_s.toFixed(1)}s · ${event.event_type} · ${event.detail}`).join("\n") || "No events yet");
    if (["complete", "terminated"].includes(payload.lifecycle)) stop();
    live.lastStep = payload.step_index;
  }

  async function control(action, withIntent) {
    if (!live.sessionId || live.busy) return;
    live.busy = true;
    try {
      const payload = await api(`/api/simulations/${live.sessionId}/control`, {method: "POST", body: JSON.stringify({action, ...(withIntent ? {intent: intent()} : {})})});
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
        const shadow_policy_id = dropdownRawValue("live-shadow-policy") || null;
        const payload = await api("/api/simulations", {method: "POST", body: JSON.stringify({scenario_id: scenario, fault, shadow_policy_id})});
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
})();
