import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Result } from "antd";

interface Props {
  children: ReactNode;
  /**
   * 局部降级渲染（不传 = 全局兜底整页错误卡）。
   * 单卡包一层，一张卡渲染异常只坏这一张，不牵连邻卡与整页
   * （背景：2026-09-21 盘中口径 `null.localeCompare` 直接把整个短线情绪 tab 换成「页面渲染出错」）。
   */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * 全局渲染错误兜底：任何子树抛错时降级为错误卡片，而不是卸载整棵 React 树
 * （React 18 单个组件渲染异常会导致整页白屏）。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary] 渲染异常:", error, info.componentStack);
  }

  private handleReset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      const { fallback } = this.props;
      if (fallback) {
        return fallback(this.state.error, this.handleReset);
      }
      return (
        <Result
          status="error"
          title="页面渲染出错"
          subTitle={this.state.error.message || "组件渲染发生异常，请重试"}
          extra={
            <>
              <Button type="primary" onClick={this.handleReset}>
                重试
              </Button>
              <Button onClick={() => window.location.assign("/")}>返回首页</Button>
            </>
          }
        />
      );
    }
    return this.props.children;
  }
}
