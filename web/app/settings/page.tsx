"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SettingsApi } from "@/lib/api";
import { OllamaConfig } from "@/components/OllamaConfig";

export default function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => SettingsApi.get() });

  // Local edits — only the keys NOT in env. env-set keys are read-only.
  const [keyEdits, setKeyEdits] = useState<Record<string, string>>({});
  // Defaults form state
  const [defaults, setDefaults] = useState<Record<string, any>>({});

  useEffect(() => {
    if (q.data?.defaults) setDefaults(q.data.defaults);
  }, [q.data]);

  const saveKeys = useMutation({
    mutationFn: () => SettingsApi.update({ api_keys: keyEdits }),
    onSuccess: () => { setKeyEdits({}); qc.invalidateQueries({ queryKey: ["settings"] }); },
  });
  const saveDefaults = useMutation({
    mutationFn: () => SettingsApi.update({ defaults }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });

  if (q.isLoading) return <div className="text-muted">Loading settings…</div>;
  const data = q.data;
  if (!data) return <div className="text-danger">Could not load settings.</div>;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted text-sm">
          Stored locally at <code>{data.config_path}</code> (chmod 0600). Never transmitted off
          the host.
        </p>
      </header>

      {/* ---- API keys ---- */}
      <section>
        <h2 className="text-lg font-semibold mb-2">API keys</h2>
        <p className="text-xs text-muted mb-3">
          Keys present in the process environment (e.g. via <code>.env</code>) always win and show
          as <strong>env</strong>. Otherwise, set them here.
        </p>
        <div className="card">
          <div className="grid grid-cols-1 gap-2">
            {data.api_keys.map((k) => {
              const editing = !k.set_in_env;
              return (
                <div
                  key={k.env_name}
                  className="flex items-center gap-3 py-1.5 border-b border-border last:border-0"
                >
                  <div className="w-44 shrink-0">
                    <div className="text-sm font-medium">{k.label}</div>
                    <code className="text-xs text-muted">{k.env_name}</code>
                  </div>
                  <input
                    type="password"
                    className="input flex-1"
                    placeholder={
                      k.set_in_env
                        ? "•••• (from environment)"
                        : k.set_in_config
                          ? "•••• (saved)"
                          : "(not set)"
                    }
                    disabled={!editing}
                    value={keyEdits[k.env_name] ?? ""}
                    onChange={(e) => setKeyEdits({ ...keyEdits, [k.env_name]: e.target.value })}
                  />
                  <span className={`pill ${k.set_in_env ? "bg-success/15 text-success" : k.set_in_config ? "bg-accent/15 text-accent" : "bg-muted/15 text-muted"}`}>
                    {k.set_in_env ? "env" : k.set_in_config ? "saved" : "empty"}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="flex justify-end mt-3">
            <button
              className="btn btn-primary"
              onClick={() => saveKeys.mutate()}
              disabled={Object.keys(keyEdits).length === 0 || saveKeys.isPending}
            >
              {saveKeys.isPending ? "Saving…" : "Save API keys"}
            </button>
          </div>
        </div>
      </section>

      {/* ---- Defaults ---- */}
      {/* ---- Ollama (local models) ---- */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Ollama (local models)</h2>
        <p className="text-xs text-muted mb-3">
          Point at an Ollama server on your LAN to run analyses entirely
          locally. The API container reaches the host via{" "}
          <code>host.docker.internal</code> if Ollama runs on the same NAS;
          otherwise put the explicit IP/hostname.
        </p>
        <div className="card">
          <OllamaConfig
            currentUrl={(defaults.ollama_base_url as string) || ""}
            onSaved={() => qc.invalidateQueries({ queryKey: ["settings"] })}
          />
        </div>
      </section>

      <PlannerIntegrationSection />

      <section>
        <h2 className="text-lg font-semibold mb-2">Default run configuration</h2>
        <p className="text-xs text-muted mb-3">
          Pre-fills the <strong>Run</strong> page form. Override per-run there.
        </p>
        <div className="card grid grid-cols-3 gap-3">
          <DefaultField name="llm_provider" label="Provider" defaults={defaults} setDefaults={setDefaults} />
          <DefaultField name="deep_think_llm" label="Deep-think model" defaults={defaults} setDefaults={setDefaults} />
          <DefaultField name="quick_think_llm" label="Quick-think model" defaults={defaults} setDefaults={setDefaults} />
          <NumberField name="max_debate_rounds" label="Bull/Bear rounds" min={1} max={5} defaults={defaults} setDefaults={setDefaults} />
          <NumberField name="max_risk_discuss_rounds" label="Risk rounds" min={1} max={5} defaults={defaults} setDefaults={setDefaults} />
          <DefaultField name="output_language" label="Output language" defaults={defaults} setDefaults={setDefaults} />
          <div className="col-span-3 flex justify-end">
            <button className="btn btn-primary" onClick={() => saveDefaults.mutate()} disabled={saveDefaults.isPending}>
              {saveDefaults.isPending ? "Saving…" : "Save defaults"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function DefaultField({
  name, label, defaults, setDefaults,
}: { name: string; label: string; defaults: Record<string, any>; setDefaults: (d: Record<string, any>) => void }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input
        className="input w-full"
        value={defaults[name] ?? ""}
        onChange={(e) => setDefaults({ ...defaults, [name]: e.target.value })}
      />
    </div>
  );
}

function NumberField({
  name, label, min, max, defaults, setDefaults,
}: { name: string; label: string; min: number; max: number; defaults: Record<string, any>; setDefaults: (d: Record<string, any>) => void }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input
        type="number"
        min={min}
        max={max}
        className="input w-full"
        value={defaults[name] ?? min}
        onChange={(e) => setDefaults({ ...defaults, [name]: Number(e.target.value) })}
      />
    </div>
  );
}

// Settings card for the financial-planner sibling integration. Lets the
// user set the planner URL + API key from the GUI instead of editing
// .env. Env vars (PLANNER_API_URL / PLANNER_API_KEY) still take precedence
// — when one is set the corresponding input is disabled and labeled "env".
// "Test connection" hits /api/health on the planner with the stored auth
// header so the user gets immediate feedback after saving.
function PlannerIntegrationSection() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["planner-integration"],
    queryFn: () => SettingsApi.getPlannerIntegration(),
  });

  const [url, setUrl] = useState<string>("");
  const [key, setKey] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);

  useEffect(() => {
    if (q.data) {
      setUrl(q.data.url);
      setKey(""); // never pre-fill the key — only show masked
      setDirty(false);
    }
  }, [q.data]);

  const save = useMutation({
    mutationFn: () =>
      SettingsApi.updatePlannerIntegration({
        // Only send fields the user actually edited. Empty string = clear.
        url: q.data?.url_set_in_env ? undefined : url,
        key: q.data?.key_set_in_env ? undefined : (dirty ? key : undefined),
      }),
    onSuccess: () => {
      setKey("");
      setDirty(false);
      setTestResult(null);
      qc.invalidateQueries({ queryKey: ["planner-integration"] });
    },
  });

  const test = useMutation({
    mutationFn: () => SettingsApi.testPlannerIntegration(),
    onSuccess: (res) => {
      setTestResult({
        ok: res.ok,
        msg: res.ok
          ? "✓ planner reachable, auth accepted"
          : `failed: ${res.error || `HTTP ${res.status_code}`}`,
      });
    },
  });

  const data = q.data;
  return (
    <section>
      <h2 className="text-lg font-semibold mb-2">Financial Planner integration</h2>
      <p className="text-xs text-muted mb-3">
        Pull holdings from a sibling Financial Planner instance into the local positions table.
        See <code>INTEGRATION.md</code>. Set the URL and an API key matching{" "}
        <code>INTEGRATION_API_KEY</code> on the planner side (generate one from the planner's
        Settings page). Env vars <code>PLANNER_API_URL</code> /{" "}
        <code>PLANNER_API_KEY</code> override these when set.
      </p>
      <div className="card space-y-3">
        <div>
          <label className="label">Planner URL</label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              className="input flex-1"
              placeholder="http://192.168.2.34:8765"
              value={url}
              disabled={!!data?.url_set_in_env}
              onChange={(e) => { setUrl(e.target.value); setDirty(true); }}
            />
            <span
              className={`pill ${data?.url_set_in_env ? "bg-success/15 text-success" : url ? "bg-accent/15 text-accent" : "bg-muted/15 text-muted"}`}
            >
              {data?.url_set_in_env ? "env" : url ? "saved" : "empty"}
            </span>
          </div>
        </div>
        <div>
          <label className="label">API key</label>
          <div className="flex items-center gap-2">
            <input
              type="password"
              className="input flex-1"
              placeholder={
                data?.key_set_in_env
                  ? "•••• (from environment)"
                  : data?.masked_key
                    ? `•••• (saved · ends in ${data.masked_key.replace(/^…/, "")})`
                    : "(not set — paste from planner Settings page)"
              }
              disabled={!!data?.key_set_in_env}
              value={key}
              onChange={(e) => { setKey(e.target.value); setDirty(true); }}
            />
            <span
              className={`pill ${data?.key_set_in_env ? "bg-success/15 text-success" : data?.masked_key ? "bg-accent/15 text-accent" : "bg-muted/15 text-muted"}`}
            >
              {data?.key_set_in_env ? "env" : data?.masked_key ? "saved" : "empty"}
            </span>
          </div>
          <p className="text-[11px] text-muted mt-1">
            Generate this key on the planner's <strong>Settings → Integration API key</strong>{" "}
            section using the &ldquo;Generate + save&rdquo; or &ldquo;Rotate key&rdquo; button,
            then reveal &amp; copy it here.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            className="btn btn-primary"
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          <button
            className="btn"
            onClick={() => test.mutate()}
            disabled={test.isPending}
          >
            {test.isPending ? "Testing…" : "Test connection"}
          </button>
          {testResult && (
            <span className={`text-xs ${testResult.ok ? "text-success" : "text-danger"}`}>
              {testResult.msg}
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
