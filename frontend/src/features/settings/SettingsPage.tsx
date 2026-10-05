import { useEffect, useState } from "react";

import { Button, Card, ErrorNotice, PageHeader, SuccessNotice } from "../../shared/ui";
import { useSaveSettings, useSettings, useTestConnection } from "./api";

export function SettingsPage() {
  const settings = useSettings();
  const save = useSaveSettings();
  const probe = useTestConnection();

  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [temperature, setTemperature] = useState("");
  const [maxTokens, setMaxTokens] = useState("");

  // 读到的配置回填表单（只做一次，不覆盖用户正在输入的内容）
  useEffect(() => {
    if (settings.data) {
      setBaseUrl((current) => current || settings.data!.base_url);
      setModel((current) => current || settings.data!.model);
      setTemperature((current) =>
        current || (settings.data!.temperature != null ? String(settings.data!.temperature) : ""),
      );
      setMaxTokens((current) =>
        current || (settings.data!.max_tokens != null ? String(settings.data!.max_tokens) : ""),
      );
    }
  }, [settings.data]);

  return (
    <>
      <PageHeader
        title="配置"
        subtitle={settings.data?.configured ? "模型已配置" : "还没配好模型 —— 助手与评分要用它"}
      />

      {settings.isError && <ErrorNotice message={(settings.error as Error).message} />}

      <Card className="max-w-2xl p-6">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate({
              base_url: baseUrl,
              model,
              // 空 key = 不改（后端语义）；这里原样传，别自作主张塞占位符
              api_key: apiKey,
              temperature: temperature.trim() === "" ? null : Number(temperature),
              max_tokens: maxTokens.trim() === "" ? null : Number(maxTokens),
            });
            setApiKey("");
          }}
        >
          <Field label="Base URL" hint="任意 OpenAI 兼容端点，如 https://api.deepseek.com/v1">
            <input
              className="w-full rounded border border-slate-300 px-3 py-2"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.example.com/v1"
              required
            />
          </Field>

          <Field label="模型">
            <input
              className="w-full rounded border border-slate-300 px-3 py-2"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="deepseek-chat"
              required
            />
          </Field>

          <Field
            label="API Key"
            hint={`留空表示「不改」。当前：${settings.data?.masked_key ?? "（未设置）"}`}
          >
            <input
              id="api_key"
              type="password"
              className="w-full rounded border border-slate-300 px-3 py-2"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="sk-…"
            />
          </Field>

          <div className="flex gap-4">
            <Field label="temperature" hint="0–2，留空用厂商默认">
              <input
                className="w-32 rounded border border-slate-300 px-3 py-2"
                value={temperature}
                onChange={(event) => setTemperature(event.target.value)}
              />
            </Field>
            <Field label="max_tokens" hint="留空用厂商默认">
              <input
                className="w-32 rounded border border-slate-300 px-3 py-2"
                value={maxTokens}
                onChange={(event) => setMaxTokens(event.target.value)}
              />
            </Field>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <Button type="submit" variant="primary" disabled={save.isPending}>
              {save.isPending ? "保存中…" : "保存"}
            </Button>
            <Button onClick={() => probe.mutate()} disabled={probe.isPending}>
              {probe.isPending ? "探测中…" : "测试连接"}
            </Button>
            {save.isSuccess && <SuccessNotice message="已保存。" />}
          </div>
        </form>

        {save.isError && (
          <div className="mt-4">
            <ErrorNotice message={(save.error as Error).message} />
          </div>
        )}
        {probe.data && (
          <div className="mt-4">
            {probe.data.ok === "1" ? (
              <SuccessNotice message={probe.data.message ?? ""} />
            ) : (
              <ErrorNotice message={probe.data.message ?? "连接失败"} />
            )}
          </div>
        )}
        {probe.isError && (
          <div className="mt-4">
            <ErrorNotice message={(probe.error as Error).message} />
          </div>
        )}
      </Card>

      <p className="mt-4 max-w-2xl text-xs text-slate-500">
        密钥明文存在本地 SQLite：单机单用户场景下，系统钥匙串会引入平台特有依赖，
        与「零托管、跨平台」冲突。界面只回显掩码，日志不输出完整密钥。
      </p>
    </>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint != null && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}
