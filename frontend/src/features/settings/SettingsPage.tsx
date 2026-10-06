import { useEffect, useRef, useState } from "react";

import { ProfileEditor } from "../scoring/components/ProfileEditor";
import { Button, Card, ErrorNotice, PageHeader, SuccessNotice } from "../../shared/ui";
import { useSaveSettings, useSettings, useTestConnection } from "./api";

export function SettingsPage() {
  const settings = useSettings();
  const save = useSaveSettings();
  const probe = useTestConnection();

  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const hydrated = useRef(false);

  // 把读到的配置回填表单 —— **只回填一次**。
  //
  // 必须用「已回填」标记，不能写成 `setX(current => current || server)`：
  // 后者表面上是「不覆盖用户输入」，实际是「首个非空值永久锁定」——
  // 用户清空某字段后，`current` 变成 ""，任何一次 refetch（保存后 invalidate、
  // 窗口焦点回归）都会把服务器旧值**静默填回去**，用户以为自己清掉了。
  //
  // 也不能只靠依赖数组：`settings.data` 每次 refetch 都是新对象，引用必变。
  useEffect(() => {
    if (!settings.data || hydrated.current) {
      return;
    }
    hydrated.current = true;
    setBaseUrl(settings.data.base_url);
    setModel(settings.data.model);
  }, [settings.data]);

  return (
    <>
      <PageHeader
        title="配置"
        subtitle={
          settings.data?.broken
            ? "已保存的配置不可用 —— 请重新填写并保存"
            : settings.data?.configured
              ? "模型已配置"
              : "还没配好模型 —— 助手与评分要用它"
        }
      />

      {settings.isError && <ErrorNotice message={(settings.error as Error).message} />}

      {/* 配置损坏：说清原因，表单仍可填（用户直接重填覆盖即可自救）。
          后端为此刻意给 200 而不是 500 —— 500 会让这个页面打不开，
          而这里恰恰是唯一的修复入口。 */}
      {settings.data?.broken && (
        <div className="mb-4">
          <ErrorNotice message="已保存的模型配置不合法（可能被改坏或来自旧版本）。下方表单已留空，重新填写并保存即可恢复。" />
        </div>
      )}

      <Card className="max-w-2xl p-6">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            // 密钥输入框只在**保存成功**后才清空。
            // 原先是在 mutate 之后同步执行 setApiKey("") —— 请求失败时密钥已被清掉，
            // 用户得把 key 重新敲一遍，而他刚被告知「模型名不合法」这种无关错误。
            save.mutate(
              {
                base_url: baseUrl,
                model,
                // 空 key = 不改（后端语义）；这里原样传，别自作主张塞占位符
                api_key: apiKey,
              },
              { onSuccess: () => setApiKey("") },
            );
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
            {probe.data.ok ? (
              <SuccessNotice message={probe.data.message} />
            ) : (
              <ErrorNotice message={probe.data.message || "连接失败"} />
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

      <div className="mt-6">
        <ProfileEditor />
      </div>
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
