import { serve } from "https://deno.land/std@0.177.0/http/server.ts";
import { Logger } from "../_shared/logger.ts";
import { v4 as uuidv4 } from 'uuid';

serve(async (req) => {
  const url = new URL(req.url);
  const functionName = "stripe-webhook";

  // Attempt to get trace_id from headers, otherwise generate a new one
  const traceId = req.headers.get("X-Trace-ID") || uuidv4();
  const logger = new Logger(functionName, traceId);

  try {
    logger.info("Incoming Stripe webhook request", {
      method: req.method,
      path: url.pathname,
      headers: Object.fromEntries(req.headers),
    });

    // In a real scenario, you'd verify the Stripe signature
    // and process the webhook event.
    // For this example, we'll just acknowledge receipt.
    const responseBody = {
      message: "Stripe webhook received",
      traceId: logger.getTraceId(),
    };

    logger.info("Outgoing Stripe webhook response", {
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
    logger.error("Error processing Stripe webhook", error, {
      requestUrl: req.url,
      requestMethod: req.method,
    });

    return new Response(JSON.stringify({ 
      error: "Internal Server Error processing Stripe webhook", 
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
