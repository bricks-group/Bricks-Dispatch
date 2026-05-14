import { v4 as uuidv4 } from "uuid";

type LogLevel = "debug" | "info" | "warn" | "error";

interface LogEntry {
  timestamp: string;
  level: LogLevel;
  function_name: string;
  trace_id: string;
  message: string;
  data?: Record<string, unknown>;
}

export class Logger {
  private traceId: string;
  private functionName: string;

  constructor(functionName: string, traceId?: string) {
    this.functionName = functionName;
    this.traceId = traceId || uuidv4();
  }

  private log(level: LogLevel, message: string, data?: Record<string, unknown>) {
    const logEntry: LogEntry = {
      timestamp: new Date().toISOString(),
      level: level,
      function_name: this.functionName,
      trace_id: this.traceId,
      message: message,
      data: data,
    };

    if (Deno.env.get("DENO_ENV") === "production") {
      console.log(JSON.stringify(logEntry));
    } else {
      // Fallback for local development
      console.log(`[${logEntry.timestamp}] [${logEntry.level.toUpperCase()}] [${logEntry.function_name}] [${logEntry.trace_id}] ${logEntry.message}` + (data ? ` ${JSON.stringify(data)}` : ""));
    }
  }

  public debug(message: string, data?: Record<string, unknown>) {
    this.log("debug", message, data);
  }

  public info(message: string, data?: Record<string, unknown>) {
    this.log("info", message, data);
  }

  public warn(message: string, data?: Record<string, unknown>) {
    this.log("warn", message, data);
  }

  public error(message: string, data?: Record<string, unknown>) {
    this.log("error", message, data);
  }
}

// Global function to get a logger instance for convenience
export function getLogger(functionName: string, traceId?: string): Logger {
  return new Logger(functionName, traceId);
}
