import { Button, Card, ErrorNotice, SuccessNotice } from "../../../shared/ui";
import { useProfile, useSaveProfile } from "../api";

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
    return <p className="text-sm text-slate-500">正在加载画像…</p>;
  }
  if (profile.isError) {
    return <ErrorNotice message={(profile.error as Error).message} />;
  }

  const current = profile.data;

  return (
    <Card className="max-w-2xl p-6">
      <h2 className="mb-1 text-lg font-medium">候选人画像</h2>
      <p className="mb-4 text-sm text-slate-500">
        {current
          ? "评分会拿它跟岗位对照。改了立刻生效，不必重启。"
          : "还没配画像 —— 评分要用它，配好之后就能给岗位打分了。"}
      </p>

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
          <span className="mb-1 block text-sm font-medium text-slate-700">目标关键词</span>
          <textarea
            name="keywords"
            rows={3}
            defaultValue={(current?.keywords ?? []).join("\n")}
            placeholder={"一行一条，如：\nAI产品经理\n大模型应用"}
            className="w-full rounded border border-slate-300 px-3 py-2 font-mono text-sm"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">目标方向</span>
          <textarea
            name="directions"
            rows={2}
            defaultValue={(current?.directions ?? []).join("\n")}
            placeholder={"一行一条，如：\nAgent 产品\nLLM 应用"}
            className="w-full rounded border border-slate-300 px-3 py-2 font-mono text-sm"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">背景摘要</span>
          <textarea
            name="summary"
            rows={4}
            defaultValue={current?.summary ?? ""}
            placeholder="一段话讲清你的背景与偏好，评分时会作为判断依据。"
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
          />
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
