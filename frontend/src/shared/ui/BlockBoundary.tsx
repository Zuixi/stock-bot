import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert, Button } from "antd";

interface Props {
  /**
   * 区块名（只进错误卡文案与 `data-block` 锚点，不参与布局）。
   * 不传 = 文案退化成「加载失败」。
   */
  title?: string;
  /**
   * 该区块的**自然高度**（px），一律取实测量到的真实渲染高度，不引入新的间距刻度。
   *
   * 只作用于**错误态**：让错误卡占住这一块本该占的空间，一块坏了下面的内容不往上抽。
   * 正常渲染时**不**加最小高度 —— 否则"合法地什么都不渲染"的块（如四项全空的
   * 公司概况、样本不足的股性行）会变成一块空白占位，"没有记录"被画成了"一块空地"。
   * 加载中的高度由各块自己的骨架负责（详见报告里的分块清单）。
   *
   * 不传 = 错误卡高度随内容（用于高度本身随数据行数变化的块，如事件表）。
   */
  minHeight?: number;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * 区块级渲染错误边界（详情页各块各一层，**不**包整页）。
 *
 * 为什么不用 `shared/ui/ErrorBoundary`：那个是**页面级**兜底（`Result` + 整页错误卡 +
 * 返回首页），它的语义是"这页坏了"。本页要的是"这张卡坏了，别的卡照常"——一段
 * 渲染期 `TypeError` 过去会把 K 线、报价条、事件表一起换掉（React 18 单个组件抛错会
 * 卸载整棵子树）。所以这里只做三件事：
 *
 * 1. `getDerivedStateFromError` 把异常收在本块内，渲染一张行内错误卡；
 * 2. 错误卡带「重试」，点击清掉错误后子树**重新挂载**。错误态渲染的是错误卡而不是
 *    `children`，子树此时已经卸载，重试时自然是全新实例（半初始化的 ref / 脏表单值随
 *    卸载消失）—— 不需要 `key` 强制换代，故也没有 `key`；
 * 3. 错误卡按 `minHeight` 占位，失败不搬家。
 *
 * 视觉上复用既有语言：`Alert type="error"` + 小号 `Button`，与 `RelatedEvents` 的
 * 行内错误块同款；不新造颜色/字号/圆角。
 */
export class BlockBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[BlockBoundary${this.props.title ? ` · ${this.props.title}` : ""}] 渲染异常:`, error, info.componentStack);
  }

  private handleRetry = () => {
    // 清掉错误即可：错误态下 `children` 已被卸载，恢复渲染时是全新的子树。
    this.setState({ error: null });
  };

  render() {
    const { title, minHeight, children } = this.props;

    if (this.state.error) {
      return (
        <div
          style={{ minHeight }}
          data-testid="block-boundary-error"
          data-block={title}
          role="alert"
        >
          <Alert
            type="error"
            showIcon
            message={title ? `${title}加载失败` : "加载失败"}
            description="这一块渲染时出错，本页其他区块不受影响。请重试。"
            action={
              <Button size="small" data-testid="block-boundary-retry" onClick={this.handleRetry}>
                重试
              </Button>
            }
          />
        </div>
      );
    }

    // 这一层 div 是**错误边界的层级**（和错误卡同层），正常渲染时不给最小高度 ——
    // 见 `minHeight` 的注释：留白占位会把"合法地什么都没有"画成一块空地。
    // 不需要 `key`：重试时 `error` 被清掉，子树从"根本没挂载"直接进入首次挂载。
    return (
      <div data-block={title}>
        {children}
      </div>
    );
  }
}
