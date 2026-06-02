/**
 * 前端日志持久化模块
 * - 支持等级 (debug / info / warn / error)
 * - 自动写入 localStorage，页面刷新不丢失
 * - 内置上限控制、过滤、导出功能
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogEntry {
  id: string;
  time: string;        // 显示用时间 HH:mm:ss
  timestamp: number;   // 排序/过滤用时间戳
  level: LogLevel;
  label: string;
  text: string;
}

export interface LoggerOptions {
  maxEntries: number;
  storageKey: string;
}

const DEFAULT_OPTIONS: LoggerOptions = {
  maxEntries: 500,
  storageKey: 'wot_event_logs',
};

const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

class Logger {
  private options: LoggerOptions;
  private logs: LogEntry[] = [];

  constructor(options?: Partial<LoggerOptions>) {
    this.options = { ...DEFAULT_OPTIONS, ...options };
    this.load();
  }

  // ─── 公开方法 ───

  debug(label: string, text: string) {
    this.append('debug', label, text);
  }

  info(label: string, text: string) {
    this.append('info', label, text);
  }

  warn(label: string, text: string) {
    this.append('warn', label, text);
  }

  error(label: string, text: string) {
    this.append('error', label, text);
  }

  /** 获取全部日志（按时间正序） */
  getAll(): LogEntry[] {
    return [...this.logs];
  }

  /**
   * 按条件过滤
   * @param levelFilter 只返回 >= 该等级的日志
   * @param keyword     文本关键字搜索 (label + text)
   */
  filter(levelFilter?: LogLevel, keyword?: string): LogEntry[] {
    let result = this.logs;
    if (levelFilter) {
      const minOrder = LEVEL_ORDER[levelFilter];
      result = result.filter((e) => LEVEL_ORDER[e.level] >= minOrder);
    }
    if (keyword) {
      const kw = keyword.toLowerCase();
      result = result.filter((e) =>
        e.label.toLowerCase().includes(kw) || e.text.toLowerCase().includes(kw)
      );
    }
    return result;
  }

  /** 清空全部日志并同步到 storage */
  clear() {
    this.logs = [];
    this.save();
  }

  /** 导出为 JSON 字符串 */
  exportJson(): string {
    return JSON.stringify(this.logs, null, 2);
  }

  /** 导出为纯文本 */
  exportText(): string {
    return this.logs
      .map((e) => `[${e.time}][${e.level.toUpperCase()}] ${e.label}: ${e.text}`)
      .join('\n');
  }

  /** 获取各等级数量统计 */
  getStats(): Record<LogLevel, number> {
    const stats: Record<LogLevel, number> = { debug: 0, info: 0, warn: 0, error: 0 };
    for (const entry of this.logs) {
      stats[entry.level]++;
    }
    return stats;
  }

  /** 更新配置（maxEntries 等） */
  setOptions(opts: Partial<LoggerOptions>) {
    Object.assign(this.options, opts);
    this.trim();
    this.save();
  }

  // ─── 内部方法 ───

  private append(level: LogLevel, label: string, text: string) {
    const entry: LogEntry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      time: new Date().toLocaleTimeString(),
      timestamp: Date.now(),
      level,
      label,
      text: String(text).slice(0, 500),
    };
    this.logs.push(entry);
    this.trim();
    this.save();
  }

  private trim() {
    if (this.logs.length > this.options.maxEntries) {
      this.logs.splice(0, this.logs.length - this.options.maxEntries);
    }
  }

  private save() {
    try {
      localStorage.setItem(this.options.storageKey, JSON.stringify(this.logs));
    } catch {
      // localStorage 可能已满，静默失败
    }
  }

  private load() {
    try {
      const raw = localStorage.getItem(this.options.storageKey);
      if (raw) {
        const parsed: LogEntry[] = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          this.logs = parsed;
        }
      }
    } catch {
      // 数据损坏时重新开始
      this.logs = [];
    }
  }
}

/** 全局单例 */
export const logger = new Logger();
