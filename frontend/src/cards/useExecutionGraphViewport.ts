import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, PointerEvent } from 'react';

type Camera = { x: number; y: number; scale: number };
const limitScale = (scale: number) => Math.min(3, Math.max(.01, scale));

/** A local camera: gestures must never move the containing card or workspace. */
export function useExecutionGraphViewport(width: number, height: number, populated: boolean) {
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, scale: 1 });
  const [autoFit, setAutoFit] = useState(true);
  const [panning, setPanning] = useState(false);
  const drag = useRef<{ id: number; x: number; y: number; camera: Camera; moved: boolean }>();
  const suppressClick = useRef(false);

  useLayoutEffect(() => {
    const element = viewport.current!;
    const measure = () => {
      const { clientWidth: width, clientHeight: height } = element;
      if (width > 0 && height > 0) setSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const fitted = useCallback(() => {
    const scale = limitScale(Math.min(1, (size.width - 24) / width, (size.height - 24) / height));
    return { scale, x: (size.width - width * scale) / 2, y: (size.height - height * scale) / 2 };
  }, [size, width, height]);
  useLayoutEffect(() => {
    if (autoFit && populated && size.width && size.height) setCamera(fitted());
  }, [autoFit, populated, size, fitted]);
  const fit = useCallback(() => { setAutoFit(true); setCamera(fitted()); }, [fitted]);
  const reset = useCallback(() => { setAutoFit(true); }, []);
  const zoom = useCallback((factor: number, anchor = { x: size.width / 2, y: size.height / 2 }) => {
    setAutoFit(false);
    setCamera(previous => {
      const scale = limitScale(previous.scale * factor), ratio = scale / previous.scale;
      return { scale, x: anchor.x - (anchor.x - previous.x) * ratio, y: anchor.y - (anchor.y - previous.y) * ratio };
    });
  }, [size]);
  useEffect(() => {
    const element = viewport.current!;
    const wheel = (event: WheelEvent) => {
      if (!populated) return;
      event.preventDefault();
      event.stopPropagation();
      const rect = element.getBoundingClientRect();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1);
      zoom(Math.exp(-Math.max(-400, Math.min(400, delta)) / 400), {
        x: (event.clientX - rect.left) * element.clientWidth / rect.width,
        y: (event.clientY - rect.top) * element.clientHeight / rect.height,
      });
    };
    // React's delegated wheel listener is passive; register locally to prevent
    // scrolling/zooming the outer workspace, including trackpad pinch events.
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [zoom, populated]);

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id !== event.pointerId) return;
    suppressClick.current = drag.current.moved;
    drag.current = undefined;
    setPanning(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  return { viewport, camera, panning, fit, reset, zoom, handlers: {
    onPointerDown(event: PointerEvent<HTMLDivElement>) {
      if (!populated || event.button !== 0 || drag.current) return;
      event.stopPropagation();
      suppressClick.current = false;
      drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, camera, moved: false };
    },
    onPointerMove(event: PointerEvent<HTMLDivElement>) {
      const start = drag.current;
      if (!start || start.id !== event.pointerId) return;
      const dx = event.clientX - start.x, dy = event.clientY - start.y;
      if (!start.moved && Math.hypot(dx, dy) < 4) return;
      event.preventDefault();
      event.stopPropagation();
      start.moved = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      setPanning(true);
      setAutoFit(false);
      const element = event.currentTarget, rect = element.getBoundingClientRect();
      setCamera({ ...start.camera, x: start.camera.x + dx * element.clientWidth / rect.width,
        y: start.camera.y + dy * element.clientHeight / rect.height });
    },
    onPointerUp: endDrag,
    onPointerCancel: endDrag,
    onLostPointerCapture: endDrag,
    onPointerLeave() {
      // A press released outside before crossing the drag threshold must not
      // leave the next gesture stuck. Active drags already have pointer capture.
      if (drag.current && !drag.current.moved) drag.current = undefined;
    },
    onClickCapture(event: MouseEvent<HTMLDivElement>) {
      if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; }
    },
    onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
      if (event.target !== event.currentTarget || !populated) return;
      const movement: Record<string, [number, number]> = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] };
      if (movement[event.key]) {
        const [x, y] = movement[event.key];
        setAutoFit(false); setCamera(value => ({ ...value, x: value.x + x, y: value.y + y }));
      } else if (event.key === '+' || event.key === '=') zoom(1.25);
      else if (event.key === '-') zoom(1 / 1.25);
      else if (event.key === '0' || event.key === 'Home') fit();
      else return;
      event.preventDefault(); event.stopPropagation();
    },
  } };
}
