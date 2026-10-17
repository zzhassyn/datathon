import type { AnchorHTMLAttributes } from "react";
import { navigate } from "../lib/urlState";
export function Link(props: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a
      {...props}
      href={props.href}
      onClick={(event) => {
        props.onClick?.(event);
        if (
          !event.defaultPrevented &&
          event.button === 0 &&
          !event.metaKey &&
          !event.ctrlKey &&
          !event.shiftKey &&
          !event.altKey &&
          props.href &&
          !props.target
        ) {
          event.preventDefault();
          navigate(props.href);
        }
      }}
    />
  );
}
