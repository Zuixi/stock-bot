import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, Modal } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/features/auth";
import { EntryForm } from "@/features/journal/components/EntryForm";
import "./JournalEntryAction.css";

/**
 * 报价条动作区里的「记一笔」（P2-2）。
 *
 * ## 为什么按钮里要判登录态（本组件存在的唯一理由）
 *
 * `/company/:symbol` 是**公开路由**（`route-manifest.ts` 的 `company-symbol` 没有
 * `requiresAuth`）。匿名用户也能看到「记一笔」，但表单要提交到 `POST /api/v1/journal`，
 * 未登录必然 401 —— 用户先认真写完 200 字判断依据，点提交才知道登不上。
 * 故未登录时**不弹表单**，直接渲染 `<Link to="/login">登录后记一笔</Link>`：
 * 把"要登录"这件事提前到点击之前，而不是提交之后。
 *
 * 会话探测是三态（见 `features/auth/context.tsx`）：`isAuthReady=false` 期间
 * `isAuthenticated` 仍是 `false`，因此首帧会短暂显示登录链接 —— **刻意选这个方向**：
 * 误把已登录用户显示成链接只是多点一次，反过来把匿名用户放进表单就是 401 白填。
 *
 * ## 提交成功后的三个动作
 *
 * 1. `message.success` —— 由 `EntryForm` 内部发（它知道"记下了"的确切语义），
 *    这里**不再发第二条**，否则一次提交弹两个绿条；
 * 2. 失效 `["journal"]` 前缀 —— 与 `JournalPanel` 的 `refreshAll` 同一个 key 族
 *    （列表 + 统计），否则用户点「已记录 · 查看」到 `/me?tab=journal` 时看到的是旧列表；
 * 3. 关 Modal + 把按钮切成「已记录 · 查看」链接（`/me?tab=journal`），**不离开本页**：
 *    记一笔是"给这只股票留个判断"，不是"跳去管理日志"。
 *
 * ## 重新打开必须是空表单
 *
 * `open && <EntryForm/>`：关闭即卸载，再打开是**新实例**（`useState` 全部回到初值）。
 * 不靠"成功回调里逐个字段 setX('')"——那只能清它记得清的那几个，`created`（同类历史卡）
 * 还会跟着上一次的条目留着。`destroyOnClose` 连同 portal 里的 DOM 一起丢掉。
 */

interface Props {
  /** 当前股票代码：作为 `EntryForm` 的 `defaultSymbol`（预填并锁定） */
  symbol: string;
}

export function JournalEntryAction({ symbol }: Props) {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [recorded, setRecorded] = useState(false);

  // 未登录（或登录态未探测完）→ 只给登录入口，不挂表单。
  if (!isAuthenticated) {
    return (
      <Link className="journal-entry-action__link" to="/login" data-testid="journal-login-link">
        登录后记一笔
      </Link>
    );
  }

  // 已经记过一笔：按钮变链接（同一个动作区不再留一个会重复弹表单的按钮）。
  if (recorded) {
    return (
      <Link className="journal-entry-action__link" to="/me?tab=journal" data-testid="journal-recorded-link">
        已记录 · 查看
      </Link>
    );
  }

  return (
    <>
      <Button size="small" data-testid="journal-entry-button" onClick={() => setOpen(true)}>
        记一笔
      </Button>
      <Modal
        title={`记一笔 · ${symbol}`}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnClose
        // 表单里有 200 字 textarea：误点遮罩就丢掉草稿太贵，遮罩不关、只认「取消」/Esc
        maskClosable={false}
        width={640}
      >
        {open && (
          <EntryForm
            defaultSymbol={symbol}
            onCreated={() => {
              // 与 JournalPanel.refreshAll 同一族 key：列表（open/due/reviewed）+ 统计
              void queryClient.invalidateQueries({ queryKey: ["journal"] });
              setRecorded(true);
              setOpen(false);
            }}
          />
        )}
      </Modal>
    </>
  );
}
