import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { MODULE_TINTS } from "./navigation";

type PointerSample = [x: number, time: number];

type UsePagerOptions = {
  count: number;
  index: number;
  reduced: boolean;
  onSettle: (index: number) => void;
  dom: PagerDom;
};

export type PagerDom = {
  shell: () => HTMLDivElement | null;
  viewport: () => HTMLDivElement | null;
  track: () => HTMLDivElement | null;
  env: () => HTMLDivElement | null;
  page: (index: number) => HTMLElement | null;
  tab: (index: number) => HTMLButtonElement | null;
  pill: () => HTMLSpanElement | null;
};

const SPRING_K = 230;
const SPRING_DAMPING = 0.86;

/**
 * 五页横向轨道的弹簧分页器。
 *
 * 连续位置 `p` 只写 DOM（transform / opacity / --tint），不触发 React 重渲染；
 * React 状态只保存「圆整后的当前页」，用于 Dock 高亮与顶栏标题。
 */
export function usePager({ count, index, reduced, onSettle, dom }: UsePagerOptions) {
  const posRef = useRef(index);
  const targetRef = useRef(index);
  const activeRef = useRef(index);
  const animRef = useRef(0);
  const [active, setActive] = useState(index);

  const apply = useCallback((p: number) => {
    const width = dom.viewport()?.clientWidth || 1;
    posRef.current = p;
    const track = dom.track();
    if (track) {
      track.style.transform = `translate3d(${-p * width}px,0,0)`;
    }
    for (let i = 0; i < count; i += 1) {
      const page = dom.page(i);
      if (!page) {
        continue;
      }
      const distance = Math.min(1, Math.abs(i - p));
      page.style.transform = distance > 0 ? `scale(${1 - 0.06 * distance})` : "";
      page.style.opacity = String(1 - 0.5 * distance);
      page.style.visibility = Math.abs(i - p) > 1.02 ? "hidden" : "visible";
    }

    const env = dom.env();
    if (env) {
      env.style.transform = `translate3d(${-p * width * 0.055}px,0,0)`;
    }

    // 先夹到合法页区间，再取「页内小数部分」。旧写法 `Math.min(1, p) - low`
    // 在 p >= 1 时恒为负（被下面夹成 0），于是第 2 页之后色值一直停在第一页。
    const clamped = Math.max(0, Math.min(count - 1, p));
    const tintLow = Math.floor(clamped);
    const tintHigh = Math.min(count - 1, tintLow + 1);
    const tintFraction = Math.max(0, Math.min(1, clamped - tintLow));
    const from = MODULE_TINTS[tintLow] ?? MODULE_TINTS[0];
    const to = MODULE_TINTS[tintHigh] ?? MODULE_TINTS[0];
    const tint = from.map((value, i) => Math.round(value + (to[i] - value) * tintFraction));
    dom.shell()?.style.setProperty("--tint", `rgb(${tint.join(" ")})`);

    const low = Math.floor(clamped);
    const high = Math.min(count - 1, low + 1);
    const fraction = Math.max(0, Math.min(1, clamped - low));
    const first = dom.tab(low);
    const second = dom.tab(high);
    const pill = dom.pill();
    if (pill && first && second) {
      const x = first.offsetLeft + (second.offsetLeft - first.offsetLeft) * fraction;
      const w = first.offsetWidth + (second.offsetWidth - first.offsetWidth) * fraction;
      const overshoot = p < 0 ? p : p > count - 1 ? p - (count - 1) : 0;
      pill.style.transform = `translate3d(${x + overshoot * 24}px,0,0)`;
      pill.style.width = `${w}px`;
    }

    const rounded = Math.max(0, Math.min(count - 1, Math.round(p)));
    if (rounded !== activeRef.current) {
      activeRef.current = rounded;
      setActive(rounded);
    }
  }, [count, dom]);

  const settle = useCallback((target: number) => {
    targetRef.current = target;
    apply(target);
    onSettle(target);
  }, [apply, onSettle]);

  const animateTo = useCallback((target: number, initialVelocity = 0) => {
    cancelAnimationFrame(animRef.current);
    const goal = Math.max(0, Math.min(count - 1, target));
    targetRef.current = goal;
    if (reduced) {
      settle(goal);
      return;
    }

    let velocity = initialVelocity;
    let last = performance.now();
    const damping = 2 * Math.sqrt(SPRING_K) * SPRING_DAMPING;
    const step = (now: number) => {
      const dt = Math.min(0.032, (now - last) / 1000);
      last = now;
      const substeps = 4;
      const h = dt / substeps;
      for (let i = 0; i < substeps; i += 1) {
        const acceleration = -SPRING_K * (posRef.current - goal) - damping * velocity;
        velocity += acceleration * h;
        posRef.current += velocity * h;
      }
      apply(posRef.current);
      if (Math.abs(posRef.current - goal) < 0.0008 && Math.abs(velocity) < 0.01) {
        settle(goal);
        return;
      }
      animRef.current = requestAnimationFrame(step);
    };
    animRef.current = requestAnimationFrame(step);
  }, [apply, count, reduced, settle]);

  useLayoutEffect(() => {
    if (Math.abs(posRef.current - index) > 0.01) {
      animateTo(index);
    } else {
      apply(index);
    }
  }, [animateTo, apply, index]);

  useEffect(() => {
    const measure = () => apply(posRef.current);
    measure();
    window.addEventListener("resize", measure);
    let observer: ResizeObserver | undefined;
    const viewport = dom.viewport();
    if (typeof ResizeObserver !== "undefined" && viewport) {
      observer = new ResizeObserver(measure);
      observer.observe(viewport);
    }
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
      cancelAnimationFrame(animRef.current);
    };
  }, [apply, dom]);

  const dragRef = useRef<{
    pointerId: number;
    x: number;
    y: number;
    start: number;
    axis: "x" | "y" | null;
    samples: PointerSample[];
  } | null>(null);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) {
      return;
    }
    const target = event.target as HTMLElement;
    if (target.closest("input,textarea,select,.mobile-no-swipe,.mobile-chips,.mobile-segmented")) {
      return;
    }
    cancelAnimationFrame(animRef.current);
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      start: posRef.current,
      axis: null,
      samples: [[event.clientX, performance.now()]],
    };
  }, []);

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (drag.axis === null) {
      if (Math.hypot(dx, dy) < 8) {
        return;
      }
      drag.axis = Math.abs(dx) > Math.abs(dy) * 1.1 ? "x" : "y";
      if (drag.axis === "y") {
        dragRef.current = null;
        return;
      }
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // jsdom / 旧浏览器不支持指针捕获：拖动仍可继续，只是离开元素会中断
      }
    }
    drag.samples.push([event.clientX, performance.now()]);
    if (drag.samples.length > 6) {
      drag.samples.shift();
    }
    const width = dom.viewport()?.clientWidth || 1;
    let next = drag.start - dx / width;
    if (next < 0) {
      next *= 0.28;
    } else if (next > count - 1) {
      next = count - 1 + (next - (count - 1)) * 0.28;
    }
    apply(next);
    event.preventDefault();
  }, [apply, count, dom]);

  const endDrag = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    dragRef.current = null;
    if (drag.axis !== "x") {
      return;
    }
    const first = drag.samples[0];
    const last = drag.samples[drag.samples.length - 1];
    const velocity = last[1] > first[1] ? (last[0] - first[0]) / (last[1] - first[1]) : 0;
    const width = dom.viewport()?.clientWidth || 1;
    const projected = Math.round(posRef.current + (-velocity * 1000 / width) * 0.18);
    const target = Math.max(
      Math.floor(drag.start) - 1,
      Math.min(Math.ceil(drag.start) + 1, projected),
    );
    animateTo(target, -velocity * 1000 / width);
  }, [animateTo, dom]);

  const bind = {
    onPointerDown,
    onPointerMove,
    onPointerUp: endDrag,
    onPointerCancel: endDrag,
  };

  const isAt = useCallback((pageIndex: number) => Math.abs(posRef.current - pageIndex) < 0.01, []);
  const scrollToTop = (pageIndex: number) => {
    dom.page(pageIndex)?.scrollTo?.({ top: 0, behavior: "smooth" });
  };

  return {
    active,
    animateTo,
    bind,
    isAt,
    scrollToTop,
  };
}
