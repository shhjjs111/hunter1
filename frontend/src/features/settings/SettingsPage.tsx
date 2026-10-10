import { useState } from "react";

import { ProfileEditor } from "../scoring/components/ProfileEditor";
import { Button, Card, ErrorNotice, PageHeader, SuccessNotice, fieldClass } from "../../shared/ui";
import { useSaveSettings, useSettings, useTestConnection } from "./api";

export function SettingsPage() {
  const settings = useSettings();
  const save = useSaveSettings();
  const probe = useTestConnection();

  // 表单值：`null` = 「用户还没动过这个字段」→ 显示服务端值。
  //
  // 用**派生**而不是「effect 回填」：effect 里 setState 会多一轮渲染（eslint 的
  // `react-hooks/set-state-in-effect` 也在拦这个模式），而且必须靠「已回填」标记
  // 加 `touched` 才能不被 refetch 覆盖 —— 慢网络下「页面一打开就输入」是常态，
  // 回填一旦盖住用户刚敲的内容就是在吃数据。派生写法天生满足两条：
  // - 没动过的字段永远跟随服务端（首次加载、保存后 refetch 都对）；
  // - 动过的字段以用户为准，包括**清空**（`""` 是有效草稿，不是「没动过」——
  //   写 `value || server` 那种「首个非空锁死」才是真错，用户清不掉的）。
  const [baseUrlEdit, setBaseUrlEdit] = useState<string | null>(null);
  const [modelEdit, setModelEdit] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const baseUrl = baseUrlEdit ?? settings.data?.base_url ?? "";
  const model = modelEdit ?? settings.data?.model ?? "";

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

      {/* 非致命提示（如明文 http:// 指向公网）：值仍可用，但用户该知道 Key 会
          明文过网。后端算的，前端只显示 —— 判断逻辑只有一处。 */}
      {settings.data?.warning && (
        <div className="mb-4">
          <ErrorNotice message={settings.data.warning} />
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
              className={`w-full ${fieldClass}`}
              value={baseUrl}
              onChange={(event) => setBaseUrlEdit(event.target.value)}
              placeholder="https://api.example.com/v1"
              required
            />
          </Field>

          <Field label="模型">
            <input
              className={`w-full ${fieldClass}`}
              value={model}
              onChange={(event) => setModelEdit(event.target.value)}
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
              className={`w-full ${fieldClass}`}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="sk-…"
            />
          </Field>

          <div className="flex items-center gap-3 pt-2">
            <Button type="submit" variant="primary" disabled={save.isPending}>
              {save.isPending ? "保存中…" : "保存"}
            </Button>
            <Button
              onClick={() => probe.mutate({ base_url: baseUrl, model, api_key: apiKey })}
              disabled={probe.isPending}
            >
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

      <p className="mt-4 max-w-2xl text-xs text-muted">
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
      <span className="mb-1 block text-sm font-medium text-ink-soft">{label}</span>
      {children}
      {hint != null && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}
