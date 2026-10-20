import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useMobileChrome } from "../../app/mobileChrome";
import { useTheme, type ThemePreference } from "../../app/theme";
import { isMobileApp } from "../../mobile";
import {
  loadSyncConfig,
  saveSyncConfig,
  syncWithDesktop,
  type SyncConfig,
  type SyncMode,
} from "../../mobile/sync";
import { MobileIcon } from "../../shared/ui/mobile";
import { useProfile, useSaveProfile } from "../scoring/api";
import { useSaveSettings, useSettings, useTestConnection } from "./api";

const THEME_OPTIONS: Array<{ value: ThemePreference; label: string }> = [
  { value: "dark", label: "深色" },
  { value: "light", label: "浅色" },
  { value: "system", label: "跟随系统" },
];

export function MobileSettingsPage() {
  const { showToast } = useMobileChrome();
  const queryClient = useQueryClient();
  const settings = useSettings();
  const save = useSaveSettings();
  const probe = useTestConnection();
  const profile = useProfile();
  const saveProfile = useSaveProfile();
  const { preference, setPreference } = useTheme();

  const [baseUrlEdit, setBaseUrlEdit] = useState<string | null>(null);
  const [modelEdit, setModelEdit] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [keywordsEdit, setKeywordsEdit] = useState<string | null>(null);
  const [directionsEdit, setDirectionsEdit] = useState<string | null>(null);
  const [summaryEdit, setSummaryEdit] = useState<string | null>(null);
  const nativeApp = isMobileApp();
  const [syncConfig, setSyncConfig] = useState<SyncConfig | null>(null);
  const [desktopUrl, setDesktopUrl] = useState("");
  const [accessCode, setAccessCode] = useState("");
  const [syncMode, setSyncMode] = useState<SyncMode | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);

  useEffect(() => {
    if (!nativeApp) {
      return;
    }
    let alive = true;
    void loadSyncConfig().then((config) => {
      if (!alive) {
        return;
      }
      setSyncConfig(config);
      // 用户可能已经开始输入：只在空值时回填，避免异步结果覆盖草稿。
      setDesktopUrl((current) => current || config.desktopUrl);
      setAccessCode((current) => current || config.accessCode);
    });
    return () => {
      alive = false;
    };
  }, [nativeApp]);

  const baseUrl = baseUrlEdit ?? settings.data?.base_url ?? "";
  const model = modelEdit ?? settings.data?.model ?? "";
  const currentProfile = profile.data?.profile ?? null;
  const keywords = keywordsEdit ?? (currentProfile?.keywords ?? []).join("\n");
  const directions = directionsEdit ?? (currentProfile?.directions ?? []).join("\n");
  const summary = summaryEdit ?? currentProfile?.summary ?? "";

  const status = settings.data?.broken
    ? "已保存的配置不可用 —— 请重新填写并保存"
    : settings.data?.configured
      ? "模型已配置"
      : "还没配好模型 —— 助手与评分要用它";

  function submitSettings() {
    probe.reset();
    save.mutate(
      { base_url: baseUrl, model, api_key: apiKey },
      {
        onSuccess: () => {
          setApiKey("");
          showToast("success", "已保存。");
        },
        onError: (error) => showToast("error", error.message),
      },
    );
  }

  async function runSync(mode: SyncMode) {
    setSyncError(null);
    setSyncMode(mode);
    try {
      const saved = await saveSyncConfig({
        desktopUrl: desktopUrl.trim(),
        accessCode: accessCode.trim(),
      });
      setSyncConfig(saved);
      const outcome = await syncWithDesktop(mode);
      setSyncConfig(await loadSyncConfig());
      await queryClient.invalidateQueries();
      showToast("success", outcome.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setSyncError(message);
      showToast("error", message);
    } finally {
      setSyncMode(null);
    }
  }

  return (
    <div>
      <h1 className="mobile-title">配置</h1>
      <p className="mobile-subtitle">{status}</p>

      {settings.isError && <p className="mobile-error mb-3">{(settings.error as Error).message}</p>}
      {settings.data?.broken && (
        <p className="mobile-error mb-3">
          已保存的模型配置不合法（可能被改坏或来自旧版本）。重新填写并保存即可恢复。
        </p>
      )}
      {settings.data?.warning && <p className="mobile-error mb-3">{settings.data.warning}</p>}

      <h2 className="mobile-group-title">模型</h2>
      <div className="mobile-card mobile-settings-group mobile-no-swipe">
        <div className="mobile-setting-field">
          <label htmlFor="mobile-base-url">Base URL</label>
          <input
            id="mobile-base-url"
            value={baseUrl}
            onChange={(event) => setBaseUrlEdit(event.target.value)}
            placeholder="https://api.example.com/v1"
            autoComplete="url"
            required
          />
        </div>
        <div className="mobile-setting-field">
          <label htmlFor="mobile-model">模型</label>
          <input
            id="mobile-model"
            value={model}
            onChange={(event) => setModelEdit(event.target.value)}
            placeholder="deepseek-chat"
            autoComplete="off"
            required
          />
        </div>
        <div className="mobile-setting-field">
          <label htmlFor="mobile-api-key">API Key</label>
          <input
            id="mobile-api-key"
            type={showKey ? "text" : "password"}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={settings.data?.masked_key || "sk-…"}
            autoComplete="off"
          />
          <button
            type="button"
            className="mobile-eye"
            aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
            onClick={() => setShowKey((value) => !value)}
          >
            <MobileIcon name={showKey ? "eyeOff" : "eye"} className="size-[18px]" />
          </button>
        </div>
      </div>

      <h2 className="mobile-group-title">候选人画像</h2>
      <div className="mobile-card mobile-settings-group mobile-no-swipe">
        {profile.isError && (
          <div className="p-3">
            <p className="mobile-error">{(profile.error as Error).message}</p>
          </div>
        )}
        {profile.data?.warning && (
          <div className="p-3">
            <p className="mobile-error">{profile.data.warning}</p>
          </div>
        )}
        <div className="mobile-setting-field">
          <label htmlFor="mobile-keywords">目标关键词（一行一条）</label>
          <textarea
            id="mobile-keywords"
            value={keywords}
            onChange={(event) => setKeywordsEdit(event.target.value)}
            placeholder={"AI产品经理\n大模型应用"}
          />
        </div>
        <div className="mobile-setting-field">
          <label htmlFor="mobile-directions">目标方向（一行一条）</label>
          <textarea
            id="mobile-directions"
            value={directions}
            onChange={(event) => setDirectionsEdit(event.target.value)}
            placeholder={"Agent 产品\nLLM 应用"}
          />
        </div>
        <div className="mobile-setting-field">
          <label htmlFor="mobile-summary">背景摘要</label>
          <textarea
            id="mobile-summary"
            value={summary}
            onChange={(event) => setSummaryEdit(event.target.value)}
            placeholder="一段话讲清你的背景与偏好，评分时会作为判断依据。"
          />
        </div>
      </div>
      <div className="mt-3">
        <button
          type="button"
          className="mobile-button block"
          disabled={saveProfile.isPending}
          onClick={() => {
            const split = (value: string) =>
              value
                .split("\n")
                .map((line) => line.trim())
                .filter((line) => line !== "");
            saveProfile.mutate(
              { keywords: split(keywords), directions: split(directions), summary },
              {
                onSuccess: () => showToast("success", "画像已保存。"),
                onError: (error) => showToast("error", error.message),
              },
            );
          }}
        >
          {saveProfile.isPending ? "保存中…" : "保存画像"}
        </button>
      </div>

      {nativeApp && (
        <>
          <h2 className="mobile-group-title">电脑同步</h2>
          <div className="mobile-card mobile-settings-group mobile-no-swipe">
            <div className="mobile-setting-field">
              <label htmlFor="mobile-sync-url">电脑地址</label>
              <input
                id="mobile-sync-url"
                value={desktopUrl}
                onChange={(event) => setDesktopUrl(event.target.value)}
                placeholder="http://192.168.1.5:8000"
                autoComplete="url"
                inputMode="url"
              />
            </div>
            <div className="mobile-setting-field">
              <label htmlFor="mobile-sync-code">访问码</label>
              <input
                id="mobile-sync-code"
                type="password"
                value={accessCode}
                onChange={(event) => setAccessCode(event.target.value)}
                placeholder="电脑端终端里显示的访问码"
                autoComplete="off"
              />
            </div>
          </div>
          {syncConfig?.lastSyncAt ? (
            <p className="mobile-sync-status">
              上次同步：{new Date(syncConfig.lastSyncAt).toLocaleString("zh-CN", { hour12: false })}
              <br />
              {syncConfig.lastResult}
            </p>
          ) : (
            <p className="mobile-sync-status">还没有同步过。</p>
          )}
          {syncError && <p className="mobile-error mt-3">{syncError}</p>}
          <div className="mobile-sync-actions">
            <button
              type="button"
              className="mobile-button primary"
              disabled={syncMode !== null}
              onClick={() => void runSync("both")}
            >
              {syncMode === "both" ? "同步中…" : "双向同步"}
            </button>
            <button
              type="button"
              className="mobile-button"
              disabled={syncMode !== null}
              onClick={() => void runSync("pull")}
            >
              {syncMode === "pull" ? "拉取中…" : "从电脑拉取"}
            </button>
            <button
              type="button"
              className="mobile-button"
              disabled={syncMode !== null}
              onClick={() => void runSync("push")}
            >
              {syncMode === "push" ? "推送中…" : "推送到电脑"}
            </button>
          </div>
          <p className="mobile-foot">
            同步岗位、投递与候选人画像；API Key 和聊天记录只留本机。
            <br />
            删除不会跨设备传播；手机与电脑需在同一 Wi-Fi，电脑端用 hunter1-lan.cmd 启动。
          </p>
        </>
      )}

      <h2 className="mobile-group-title">外观</h2>
      <div className="mobile-card mobile-settings-group mobile-no-swipe">
        <div className="mobile-setting-field">
          <ThemeSegmented value={preference} onChange={setPreference} />
        </div>
      </div>

      <div className="mobile-settings-actions">
        <button
          type="button"
          className="mobile-button"
          disabled={probe.isPending}
          onClick={() => {
            save.reset();
            probe.mutate({ base_url: baseUrl, model, api_key: apiKey });
          }}
        >
          {probe.isPending ? "测试中…" : "测试连接"}
        </button>
        <button type="button" className="mobile-button primary" disabled={save.isPending} onClick={submitSettings}>
          {save.isPending ? "保存中…" : "保存"}
        </button>
      </div>
      <div className="mt-2 min-h-[18px] px-1.5 text-[13px]">
        {probe.data?.ok && <span className="mobile-success">● {probe.data.message}</span>}
        {probe.data && !probe.data.ok && <span className="text-danger">{probe.data.message || "连接失败"}</span>}
        {probe.isError && <span className="text-danger">{(probe.error as Error).message}</span>}
      </div>

      <p className="mobile-foot">
        所有数据只存在本机，API Key 只回显掩码。<br />
        密钥明文存在本地 SQLite，日志不输出完整密钥。
      </p>
    </div>
  );
}

function ThemeSegmented({
  value,
  onChange,
}: {
  value: ThemePreference;
  onChange: (value: ThemePreference) => void;
}) {
  const index = Math.max(0, THEME_OPTIONS.findIndex((option) => option.value === value));

  return (
    <div className="mobile-segmented" role="group" aria-label="外观主题">
      <span
        className="mobile-segmented-knob"
        aria-hidden="true"
        style={{ transform: `translateX(${index * 100}%)` }}
      />
      {THEME_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          className={value === option.value ? "on" : ""}
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
