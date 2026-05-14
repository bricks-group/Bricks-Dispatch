import { serve } from "https://deno.land/std@0.177.0/http/server.ts";
import { createLogger } from '../_shared/logger.ts';

serve(async (req) => {
  const url = new URL(req.url);
  const functionName = "dispatch-booking";
  
  // Attempt to get trace_id from headers, otherwise generate a new one
  // The logger will generate a new UUID if no traceId is provided.
  const logger = createLogger(functionName, req.headers.get("X-Trace-ID") || undefined);

  try {
    logger.info("Incoming request", {
      method: req.method,
      path: url.pathname,
      headers: Object.fromEntries(req.headers),
    });

    // Dummy response for now
    const responseBody = {
      message: "Processing dispatch booking request",
      traceId: logger.getTraceId(),
    };

    logger.info("Outgoing response", {
      status: 200,
      body: responseBody,
    });

    return new Response(JSON.stringify(responseBody), {
      headers: { 
        "Content-Type": "application/json",
        "X-Trace-ID": logger.getTraceId(),
      },
      status: 200,
    });
  } catch (error) {
    logger.error("Error processing request", error, {
      requestUrl: req.url,
      requestMethod: req.method,
    });

    return new Response(JSON.stringify({ 
      error: "Internal Server Error", 
      traceId: logger.getTraceId(),
    }), {
      headers: { 
        "Content-Type": "application/json",
        "X-Trace-ID": logger.getTraceId(),
      },
      status: 500,
    });
  }
});
