import { Button, Card, ErrorNotice, SuccessNotice, fieldClass } from "../../../shared/ui";
import { useProfile, useSaveProfile } from "../api";

// 与后端 models.py 的上限保持一致（后端是**闸门**，这里只是提前告知，免得白填一遍）。
// 导出是为了让测试拿契约快照逐条比对 —— 这四个值是复制来的，会漂移。
export const MAX_KEYWORDS = 50;
export const MAX_DIRECTIONS = 20;
export const MAX_ITEM_CHARS = 100;
export const MAX_SUMMARY_CHARS = 2000;

/** 三行文本 ↔ 列表：接口收 `string[]`，界面按「一行一条」编辑更顺手。 */
export function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/**
 * 候选人画像编辑 —— 评分的输入。
 *
 * 画像没配之前评分端点是「不可用但存在」的（后端给 409 + 指引），
 * 所以这里要能表达「还没配」这个初始状态，而不是显示一个空表单让人以为配过。
 */
export function ProfileEditor() {
  const profile = useProfile();
  const save = useSaveProfile();

  if (profile.isLoading) {
    return <p className="text-sm text-muted">正在加载画像…</p>;
  }

  const current = profile.data?.profile ?? null;
  const warning = profile.data?.warning ?? null;

  return (
    <Card className="max-w-2xl p-6">
      <h2 className="mb-1 text-lg font-medium">候选人画像</h2>
      <p className="mb-4 text-sm text-muted">
        {current
          ? "评分会拿它跟岗位对照。改了立刻生效，不必重启。"
          : "还没配画像 —— 评分要用它，配好之后就能给岗位打分了。"}
      </p>

      {/* 读取失败：说明原因，但**不卸载表单**。
          三个 textarea 是非受控的（`defaultValue`），整块早返回会把用户敲进去、
          还没保存的内容一起抹掉，屏幕上只剩一条错误提示 —— 而全局 staleTime 30 秒 +
          retry 会在一轮后台 refetch（切走窗口再切回）失败时触发。换句话说，
          「错误提示」与「表单可用」必须同屏。隔壁 SettingsPage 用派生值避开同一个坑，
          这里对齐它的取舍：错误内联显示，表单照留（用户能重填覆盖）。 */}
      {profile.isError && (
        <div className="mb-4">
          <ErrorNotice message={(profile.error as Error).message} />
        </div>
      )}

      {/* 存储里的画像不可用：说明原因，表单仍可用，用户直接重填覆盖即可 */}
      {warning && (
        <div className="mb-4">
          <ErrorNotice message={warning} />
        </div>
      )}

      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          save.mutate({
            keywords: splitLines(String(form.get("keywords") ?? "")),
            directions: splitLines(String(form.get("directions") ?? "")),
            summary: String(form.get("summary") ?? ""),
          });
        }}
      >
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-ink-soft">目标关键词</span>
          <textarea
            name="keywords"
            rows={3}
            defaultValue={(current?.keywords ?? []).join("\n")}
            placeholder={"一行一条，如：\nAI产品经理\n大模型应用"}
            className={`w-full font-mono text-sm ${fieldClass}`}
          />
          <span className="mt-1 block text-xs text-muted">
            一行一条，最多 {MAX_KEYWORDS} 条、每条不超过 {MAX_ITEM_CHARS} 字符 ——
            它会原样进评分提示词，太长会顶掉模型额度
          </span>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-ink-soft">目标方向</span>
          <textarea
            name="directions"
            rows={2}
            defaultValue={(current?.directions ?? []).join("\n")}
            placeholder={"一行一条，如：\nAgent 产品\nLLM 应用"}
            className={`w-full font-mono text-sm ${fieldClass}`}
          />
          <span className="mt-1 block text-xs text-muted">
            一行一条，最多 {MAX_DIRECTIONS} 条、每条不超过 {MAX_ITEM_CHARS} 字符
          </span>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-ink-soft">背景摘要</span>
          <textarea
            name="summary"
            rows={4}
            maxLength={MAX_SUMMARY_CHARS}
            defaultValue={current?.summary ?? ""}
            placeholder="一段话讲清你的背景与偏好，评分时会作为判断依据。"
            className={`w-full text-sm ${fieldClass}`}
          />
          <span className="mt-1 block text-xs text-muted">最多 {MAX_SUMMARY_CHARS} 字符</span>
        </label>

        <div className="flex items-center gap-3 pt-2">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {save.isPending ? "保存中…" : "保存画像"}
          </Button>
          {save.isSuccess && <SuccessNotice message="已保存。" />}
        </div>
      </form>

      {save.isError && (
        <div className="mt-4">
          <ErrorNotice message={(save.error as Error).message} />
        </div>
      )}
    </Card>
  );
}
