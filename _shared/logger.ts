
import { v4 as uuidv4 } from 'uuid';

type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR';

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
    const entry: LogEntry = {
      timestamp: new Date().toISOString(),
      level,
      function_name: this.functionName,
      trace_id: this.traceId,
      message,
      ...(data && { data }),
    };

    if (Deno.env.get('NODE_ENV') === 'production') {
      console.log(JSON.stringify(entry));
    } else {
      // Fallback for local development
      const dataString = data ? JSON.stringify(data) : '';
      console.log(`[${entry.timestamp}] [${entry.level}] [${entry.function_name}] [${entry.trace_id}] ${entry.message} ${dataString}`);
    }
  }

  debug(message: string, data?: Record<string, unknown>) {
    this.log('DEBUG', message, data);
  }

  info(message: string, data?: Record<string, unknown>) {
    this.log('INFO', message, data);
  }

  warn(message: string, data?: Record<string, unknown>) {
    this.log('WARN', message, data);
  }

  error(message: string, data?: Record<string, unknown>) {
    this.log('ERROR', message, data);
  }

  getTraceId(): string {
    return this.traceId;
  }
}
