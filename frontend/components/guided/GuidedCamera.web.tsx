/** Web build of the Expo app: the guided camera is native-only. go.brxs.ca has its own web flow. */
import React, { forwardRef } from 'react';
import { View, StyleSheet } from 'react-native';
import type { CaptureGuide, LockKind } from '../../lib/guided-capture/guides';

export type LockStatus = 'locked' | 'metering-locked' | 'auto' | 'unsupported' | 'failed';
export type LockState = Record<LockKind, LockStatus>;
export interface CameraInfo {
  lens: 'ultra-wide' | 'wide'; lensLabel: string; width: number | null; height: number | null; fps: number | null;
  stabilization: string | null; hdr: boolean; codec: string; bitrate: number; lockable: Record<LockKind, 'lock' | 'metering' | 'none'>;
}
export interface GuidedCameraHandle {
  lock(): Promise<LockState>; unlock(): Promise<void>;
  start(onFinished: (filePath: string, reason: string) => void, onError: (e: Error) => void): Promise<void>;
  stop(): Promise<void>; cancel(): Promise<void>;
}
export const NATIVE_GUIDED_CAMERA = false;
export function useGuidedCameraPermission() {
  return { granted: false, canAsk: false, request: async () => false, status: 'unavailable' };
}
export const GuidedCamera = forwardRef<GuidedCameraHandle, { guide: CaptureGuide; isActive: boolean; maxBytes: number; style?: any; onReady?: (i: CameraInfo) => void; onError?: (m: string) => void }>(
  function GuidedCamera(_props, _ref) { return <View style={StyleSheet.absoluteFill} />; },
);
