import { Button, Card, EmptyState, ErrorNotice, PageHeader, Tag } from "../../shared/ui";
import { STAGE_LABELS, STAGE_ORDER, useApplications, useChangeStage, useDeleteApplication } from "./api";

export function ApplicationsPage() {
  const applications = useApplications();
  const changeStage = useChangeStage();
  const remove = useDeleteApplication();

  return (
    <>
      <PageHeader
        title="投递记录"
        subtitle={applications.data ? `共 ${applications.data.length} 条` : "加载中…"}
      />

      {applications.isError && <ErrorNotice message={(applications.error as Error).message} />}
      {changeStage.isError && <ErrorNotice message={(changeStage.error as Error).message} />}
      {remove.isError && <ErrorNotice message={(remove.error as Error).message} />}

      {applications.isLoading ? (
        // 显式加载态：否则首帧会渲染一张空表，与「一条都没有」看起来一样
        <Card>
          <p className="px-4 py-6 text-sm text-slate-500">加载中…</p>
        </Card>
      ) : applications.data?.length === 0 ? (
        <EmptyState>还没有投递记录。去「岗位库」找岗位，点「记录投递」。</EmptyState>
      ) : (
        <Card>
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-slate-500">
                <th className="px-4 py-2 font-medium">公司</th>
                <th className="px-4 py-2 font-medium">岗位</th>
                <th className="px-4 py-2 font-medium">阶段</th>
                <th className="px-4 py-2 font-medium">更新</th>
                <th className="px-4 py-2 font-medium">备注</th>
                <th className="px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {(applications.data ?? []).map((item) => (
                <tr key={item.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-2">{item.company}</td>
                  <td className="px-4 py-2">{item.title}</td>
                  <td className="px-4 py-2">
                    <select
                      className="rounded border border-slate-300 bg-white px-2 py-1"
                      value={item.stage}
                      onChange={(event) =>
                        changeStage.mutate({
                          applicationId: item.id,
                          stage: event.target.value,
                          note: item.note ?? undefined,
                        })
                      }
                    >
                      {STAGE_ORDER.map((stage) => (
                        <option key={stage} value={stage}>
                          {STAGE_LABELS[stage]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-2 text-slate-500">{item.updated_at.slice(0, 10)}</td>
                  <td className="px-4 py-2 text-slate-500">{item.note ?? "—"}</td>
                  <td className="px-4 py-2 text-right">
                    <Button
                      onClick={() => {
                        // 删除不可撤销（后端无软删）—— 先确认，避免误点丢记录
                        if (
                          window.confirm(
                            `删除「${item.company}」的投递记录？此操作不可撤销。`,
                          )
                        ) {
                          remove.mutate(item.id);
                        }
                      }}
                    >
                      删除
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <p className="mt-4 text-xs text-slate-500">
        投递记录里的公司名与岗位名是<b>下单时刻的快照</b> —— 岗位被重抓或改名时，
        这里仍保留当时的说法。
      </p>
      <Tag>只读提示：阶段可直接在下拉框里改，删除不可撤销。</Tag>
    </>
  );
}
