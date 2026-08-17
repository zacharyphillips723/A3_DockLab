window.dash_clientside = Object.assign({}, window.dash_clientside, {
  a3docklab: {
    renderTime: function (time, data) {
      if (!data || !data.truth || !data.truth.event_time_ns.length) return "No run selected";
      const truth = data.truth;
      const targetNs = (time || 0) * 1e9;
      let lo = 0, hi = truth.event_time_ns.length - 1;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        if (truth.event_time_ns[mid] <= targetNs) lo = mid; else hi = mid - 1;
      }
      const i = lo;
      const rotateX = (prefix) => {
        const w = +truth[prefix + "qw"][i], x = +truth[prefix + "qx"][i];
        const y = +truth[prefix + "qy"][i], z = +truth[prefix + "qz"][i];
        return [1 - 2 * (y*y + z*z), 2 * (x*y + w*z), 2 * (x*z - w*y)];
      };
      const position = [+truth.x_m[i], +truth.y_m[i], +truth.z_m[i]];
      const scale = Math.max(1, Math.min(10, +truth.range_m[i] * 0.12));
      const chaserAxis = rotateX("chaser_");
      const targetAxis = rotateX("target_");
      const trajectory = document.getElementById("trajectory-graph");
      if (trajectory && trajectory.querySelector(".js-plotly-plot")) {
        const plot = trajectory.querySelector(".js-plotly-plot");
        const keepOutViolation = +truth.keep_out_margin_m[i] < 0;
        const corridorViolation = +truth.corridor_margin_m[i] < 0;
        Plotly.restyle(plot, {
          x: [[[position[0]]], [[position[0], position[0] + scale*chaserAxis[0]]], [[0, scale*targetAxis[0]]]],
          y: [[[position[1]]], [[position[1], position[1] + scale*chaserAxis[1]]], [[0, scale*targetAxis[1]]]],
          z: [[[position[2]]], [[position[2], position[2] + scale*chaserAxis[2]]], [[0, scale*targetAxis[2]]]],
          "marker.color": [[keepOutViolation || corridorViolation ? "#ff496c" : "#5ee7ff"], null, null]
        }, [1, 4, 5]);
        const safetyState = `${keepOutViolation}:${corridorViolation}`;
        if (plot._a3SafetyState !== safetyState) {
          const keepOutColor = keepOutViolation ? "#ff496c" : "#3772ff";
          const corridorColor = corridorViolation ? "#ff496c" : "#21d19f";
          Plotly.restyle(plot, {colorscale: [[[0,keepOutColor],[1,keepOutColor]], [[0,corridorColor],[1,corridorColor]]]}, [6, 7]);
          plot._a3SafetyState = safetyState;
        }
      }
      ["timeline-graph", "health-graph"].forEach((id) => {
        const host = document.getElementById(id);
        if (host && host.querySelector(".js-plotly-plot")) Plotly.relayout(host.querySelector(".js-plotly-plot"), {"shapes[0].x0": time, "shapes[0].x1": time});
      });
      const closing = +truth.closing_rate_m_s[i];
      const ttc = closing > 1e-6 ? (+truth.range_m[i] / closing) : Infinity;
      return `${data.scenario_id}  ·  ${(+truth.range_m[i]).toFixed(2)} m range  ·  ${closing.toFixed(3)} m/s closing  ·  ${truth.phase[i]}  ·  TTC ${Number.isFinite(ttc) ? ttc.toFixed(1) + " s" : "—"}  ·  propellant ${(+truth.propellant_used_kg[i]).toFixed(2)} kg`;
    }
  }
});
