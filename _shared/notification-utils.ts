import { globalLogger as logger } from './logger';

export async function retry<T>(
  fn: () => Promise<T>,
  retries = 3,
  delay = 1000, // milliseconds
  logMessage: string = 'Operation failed, retrying...'
): Promise<T | undefined> {
  let attempts = 0;
  while (attempts < retries) {
    try {
      return await fn();
    } catch (error: any) {
      attempts++;
      logger.warn(`${logMessage} Attempt ${attempts}/${retries}. Error: ${error.message}`);
      if (attempts < retries) {
        await new Promise(resolve => setTimeout(resolve, delay));
        delay *= 2; // Exponential backoff
      } else {
        logger.error(`Operation failed after ${retries} attempts. Last error: ${error.message}`, error);
        return undefined; // Or throw a custom error
      }
    }
  }
  return undefined;
}
