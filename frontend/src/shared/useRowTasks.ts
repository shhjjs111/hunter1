import { useCallback, useRef, useState } from "react";

/**
 * 按行（或任意 key）维护异步任务的 pending / error。
 *
 * 页面级共用一个 `useMutation` 时，`isPending` / `isError` 只反映**最后一次**
 * 调用：前一行保存失败会被后一行的成功覆盖，按钮也只锁住最后点的那一行。
 * 这里把状态按 key 分开，并用 ref 做同步闩锁 —— 连点两次不会发出两个请求。
 */
export function useRowTasks() {
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const pendingRef = useRef<Set<string>>(new Set());

  const begin = useCallback((key: string): boolean => {
    if (pendingRef.current.has(key)) {
      return false;
    }
    pendingRef.current.add(key);
    setPending(new Set(pendingRef.current));
    setErrors((prev) => {
      if (!(key in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[key];
      return next;
    });
    return true;
  }, []);

  const settle = useCallback((key: string) => {
    pendingRef.current.delete(key);
    setPending(new Set(pendingRef.current));
  }, []);

  const setError = useCallback((key: string, message: string) => {
    setErrors((prev) => ({ ...prev, [key]: message }));
  }, []);

  const clearError = useCallback((key: string) => {
    setErrors((prev) => {
      if (!(key in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  return {
    pending,
    errors,
    has: (key: string) => pending.has(key),
    errorOf: (key: string) => errors[key],
    begin,
    settle,
    setError,
    clearError,
  };
}
