/**
 * Camera HUD kit for every GO capture screen (360 tour, floor plan, Visit video).
 * GO dark rules: flat panels (no glow, no gradients), 2px corners, square shutter,
 * mono data readouts, brick accent only for the thing that matters right now.
 * Coaching sits on an opaque-enough panel so it stays legible over any room.
 */
import React from 'react';
import { View, Text, Pressable, Image, StyleSheet, Platform, type StyleProp, type ViewStyle } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { X, Camera, type LucideIcon } from 'lucide-react-native';
import { color, type as T, space, radius, layout, hairline, toneColors, type Tone } from '../theme';
import { Button, EmptyState } from './ui';
import { PressScale } from './motion';

export const hud = {
  panel: 'rgba(20, 18, 16, 0.88)',     // night @ 88%: coaching + readouts
  panelSoft: 'rgba(20, 18, 16, 0.62)', // secondary readouts
  scrimTop: 'rgba(20, 18, 16, 0.55)',
  scrimBottom: 'rgba(20, 18, 16, 0.70)',
} as const;

const SHUTTER = 76;
const SHUTTER_INNER = 60;
const STOP = 28;

/**
 * SCREENSHOT CAPTURE ONLY. When the web export is built with EXPO_PUBLIC_SCREENSHOT_FEED (an image URL),
 * a still interior photo covers the camera preview so store/contact-sheet shots don't show the headless
 * browser's synthetic test pattern. Renders nothing on iOS/Android and in normal web builds.
 */
const SCREENSHOT_FEED = Platform.OS === 'web' ? process.env.EXPO_PUBLIC_SCREENSHOT_FEED : undefined;
export function ScreenshotFeed() {
  if (!SCREENSHOT_FEED) return null;
  return <Image source={{ uri: SCREENSHOT_FEED }} resizeMode="cover" style={StyleSheet.absoluteFill} accessibilityElementsHidden />;
}

/** Flat bands behind the top bar and the controls so white text never sits on a bright window. */
export function HudScrims({ top = 132, bottom = 220 }: { top?: number; bottom?: number }) {
  return (
    <>
      <View pointerEvents="none" style={[s.scrimTop, { height: top }]} />
      <View pointerEvents="none" style={[s.scrimBottom, { height: bottom }]} />
    </>
  );
}

export function HudTopBar({ title, subtitle, subtitleTone = 'accent', onClose, right }: {
  title: string; subtitle?: string; subtitleTone?: Tone; onClose: () => void; right?: React.ReactNode;
}) {
  return (
    <View style={s.topBar}>
      <HudIconButton icon={X} label="Close" onPress={onClose} />
      <View style={s.topTitle}>
        <Text style={[T.callout, { color: color.text }]} numberOfLines={1}>{title}</Text>
        {subtitle ? (
          <View style={s.topSub}>
            <View style={[s.topSubMark, { backgroundColor: toneColors[subtitleTone].fg }]} />
            <Text style={[T.overline, { color: color.textBody }]} numberOfLines={1}>{subtitle}</Text>
          </View>
        ) : null}
      </View>
      {right ?? <View style={s.iconSpacer} />}
    </View>
  );
}

export function HudIconButton({ icon: Icon, label, onPress, active }: {
  icon: LucideIcon; label: string; onPress?: () => void; active?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      hitSlop={space[4]}
      style={({ pressed }) => [s.iconBtn, active && s.iconBtnActive, pressed && s.pressed]}
    >
      <Icon size={20} color={active ? color.textOnAccent : color.text} strokeWidth={layout.iconStroke} />
    </Pressable>
  );
}

/** Mono data strip: LABEL value · LABEL value. Tone colours only the value. */
export function HudReadout({ items, style }: {
  items: { label: string; value: string; tone?: Tone }[]; style?: StyleProp<ViewStyle>;
}) {
  return (
    <View style={[s.readout, style]} accessibilityRole="text">
      {items.map((it, i) => (
        <View key={it.label} style={[s.readoutCell, i > 0 && s.readoutDivider]}>
          <Text style={[T.tabLabel, { color: color.textFaint }]}>{it.label}</Text>
          <Text style={[T.data, { color: it.tone ? toneColors[it.tone].fg : color.text }]}>{it.value}</Text>
        </View>
      ))}
    </View>
  );
}

/** Coaching overlay: dark panel, 2px tone rule on the left, short title + one line. */
export function CoachCard({ title, body, step, tone = 'accent', style }: {
  title: string; body?: string; step?: string; tone?: Tone; style?: StyleProp<ViewStyle>;
}) {
  return (
    <View style={[s.coach, style]} accessibilityLiveRegion="polite">
      <View style={[s.coachRule, { backgroundColor: toneColors[tone].fg }]} />
      <View style={s.coachText}>
        {step ? <Text style={[T.overline, { color: toneColors[tone].fg }]}>{step}</Text> : null}
        <Text style={[T.headline, { color: color.text }]}>{title}</Text>
        {body ? <Text style={[T.bodySm, { color: color.textBody }]}>{body}</Text> : null}
      </View>
    </View>
  );
}

/**
 * Square shutter (no round buttons in Bricks). photo = brick square;
 * record = brick square that shrinks to a stop square while recording.
 */
export function Shutter({ mode = 'photo', active, busy, disabled, onPress, label }: {
  mode?: 'photo' | 'record'; active?: boolean; busy?: boolean; disabled?: boolean; onPress: () => void; label?: string;
}) {
  const a11y = label ?? (mode === 'record' ? (active ? 'Stop recording' : 'Start recording') : 'Take photo');
  return (
    <PressScale onPress={onPress} disabled={disabled || busy} accessibilityRole="button" accessibilityLabel={a11y}>
      <View style={[s.shutter, (disabled || busy) && s.shutterDisabled]}>
        <View style={[s.shutterInner, mode === 'record' && active && s.shutterStop]} />
      </View>
    </PressScale>
  );
}

/** Square secondary control with a mono label (e.g. NEXT ROOM, NEW STATION, DONE). */
export function HudControl({ icon: Icon, label, onPress, disabled, tone }: {
  icon: LucideIcon; label: string; onPress?: () => void; disabled?: boolean; tone?: Tone;
}) {
  const fg = tone ? toneColors[tone].fg : color.text;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => [s.control, disabled && s.controlDisabled, pressed && s.pressed]}
    >
      <View style={[s.controlBox, tone ? { borderColor: fg } : null]}>
        <Icon size={20} color={fg} strokeWidth={layout.iconStroke} />
      </View>
      <Text style={[T.tabLabel, { color: disabled ? color.textFaint : color.textBody }]} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

export function HudControls({ left, center, right }: { left?: React.ReactNode; center: React.ReactNode; right?: React.ReactNode }) {
  return (
    <View style={s.controls}>
      <View style={s.controlSlot}>{left}</View>
      {center}
      <View style={s.controlSlot}>{right}</View>
    </View>
  );
}

export function CameraPermission({ onAllow, onClose, body }: { onAllow: () => void; onClose: () => void; body?: string }) {
  return (
    <SafeAreaView style={s.permission}>
      <View style={s.permTop}><HudIconButton icon={X} label="Close" onPress={onClose} /></View>
      <EmptyState
        icon={Camera}
        title="Camera access"
        body={body ?? 'GO uses the camera only while you capture this listing.'}
        action={<Button title="Allow camera" onPress={onAllow} />}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  scrimTop: { position: 'absolute', top: 0, left: 0, right: 0, backgroundColor: hud.scrimTop },
  scrimBottom: { position: 'absolute', bottom: 0, left: 0, right: 0, backgroundColor: hud.scrimBottom },

  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: layout.gutter, paddingTop: space[8], gap: space[12],
  },
  topTitle: { flex: 1, alignItems: 'center' },
  topSub: { flexDirection: 'row', alignItems: 'center', gap: space[6], marginTop: 2 },
  topSubMark: { width: 6, height: 6, borderRadius: 1 },
  iconSpacer: { width: layout.tap, height: layout.tap },
  iconBtn: {
    width: layout.tap, height: layout.tap, borderRadius: radius.sm, alignItems: 'center', justifyContent: 'center',
    backgroundColor: hud.panelSoft, borderWidth: hairline, borderColor: color.hairStrong,
  },
  iconBtnActive: { backgroundColor: color.accent, borderColor: color.accent },
  pressed: { opacity: 0.7 },

  readout: {
    flexDirection: 'row', alignSelf: 'center', backgroundColor: hud.panel,
    borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hair,
  },
  readoutCell: { paddingHorizontal: space[12], paddingVertical: space[8], gap: 2, alignItems: 'flex-start' },
  readoutDivider: { borderLeftWidth: hairline, borderLeftColor: color.hair },

  coach: {
    flexDirection: 'row', marginHorizontal: layout.gutter, backgroundColor: hud.panel,
    borderRadius: radius.sm, overflow: 'hidden',
  },
  coachRule: { width: 2 },
  coachText: { flex: 1, paddingHorizontal: space[14], paddingVertical: space[12], gap: space[4] },

  shutter: {
    width: SHUTTER, height: SHUTTER, borderRadius: radius.sm, borderWidth: 2, borderColor: color.text,
    alignItems: 'center', justifyContent: 'center',
  },
  shutterDisabled: { opacity: 0.45 },
  shutterInner: { width: SHUTTER_INNER, height: SHUTTER_INNER, borderRadius: radius.sm, backgroundColor: color.accent },
  shutterStop: { width: STOP, height: STOP },

  controls: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: layout.gutter, paddingTop: space[16], paddingBottom: space[12],
  },
  controlSlot: { width: 84, alignItems: 'center' },
  control: { alignItems: 'center', gap: space[6], minWidth: layout.tap },
  controlDisabled: { opacity: 0.5 },
  controlBox: {
    width: layout.control, height: layout.control, borderRadius: radius.sm, alignItems: 'center', justifyContent: 'center',
    backgroundColor: hud.panelSoft, borderWidth: hairline, borderColor: color.hairStrong,
  },

  permission: { flex: 1, backgroundColor: color.bg },
  permTop: { paddingHorizontal: layout.gutter, paddingTop: space[8] },
});

/** Pipeline stages as a vertical list with square markers (no round dots). */
export function PipelineSteps({ steps }: { steps: { title: string; detail?: string | null; status: string }[] }) {
  return (
    <View>
      {steps.map((p, i) => {
        const done = p.status === 'done';
        const active = p.status === 'active';
        const last = i === steps.length - 1;
        return (
          <View key={`${p.title}-${i}`} style={ps.row}>
            <View style={ps.rail}>
              <View style={[ps.marker, done && ps.markerDone, active && ps.markerActive]} />
              {!last ? <View style={[ps.line, done && ps.lineDone]} /> : null}
            </View>
            <View style={ps.text}>
              <View style={ps.titleRow}>
                <Text style={[T.callout, { color: p.status === 'todo' ? color.textMuted : color.text, flex: 1 }]}>{p.title}</Text>
                {active ? <Text style={[T.tabLabel, { color: color.accentText }]}>In progress</Text> : null}
              </View>
              {p.detail ? <Text style={[T.bodySm, { color: color.textMuted }]}>{p.detail}</Text> : null}
            </View>
          </View>
        );
      })}
    </View>
  );
}

const ps = StyleSheet.create({
  row: { flexDirection: 'row', gap: space[12] },
  rail: { width: 12, alignItems: 'center', paddingTop: space[4] },
  marker: { width: 12, height: 12, borderRadius: radius.sm, borderWidth: 1.5, borderColor: color.textFaint },
  markerDone: { backgroundColor: color.ok, borderColor: color.ok },
  markerActive: { backgroundColor: color.accent, borderColor: color.accent },
  line: { width: 1.5, flex: 1, minHeight: space[20], backgroundColor: color.hairStrong, marginVertical: space[4] },
  lineDone: { backgroundColor: color.ok },
  text: { flex: 1, paddingBottom: space[16], gap: 2 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: space[8] },
});
