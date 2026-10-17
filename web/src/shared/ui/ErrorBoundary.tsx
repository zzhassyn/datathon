import { Component, type ReactNode } from "react";
import { ru } from "../i18n/ru";
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="empty-state" role="alert">
        <h1>{ru.crash}</h1>
        <button type="button" onClick={() => location.reload()}>
          {ru.reload}
        </button>
      </div>
    ) : (
      this.props.children
    );
  }
}
