/**
 * Bricks GO UI kit. Every screen composes these; no screen should hand-roll
 * a button, row, header, field or pill. All values come from ../theme.
 */
import React from 'react';
import {
  View, Text, TextInput, ScrollView, ActivityIndicator, Pressable,
  StyleSheet, type TextInputProps, type ViewStyle, type StyleProp, type TextStyle,
  type ScrollViewProps,
} from 'react-native';
import { SafeAreaView, type Edge } from 'react-native-safe-area-context';
import { ChevronLeft, ChevronRight, X, type LucideIcon } from 'lucide-react-native';
import { color, type as T, space, radius, layout, hairline, toneColors, type Tone } from '../theme';
import { PressScale } from './motion';

/* ───────────────── Text helpers ───────────────── */

export function Overline({ children, tone = 'muted', style }: {
  children: React.ReactNode; tone?: 'muted' | 'faint' | 'accent'; style?: StyleProp<TextStyle>;
}) {
  const c = tone === 'accent' ? color.accentText : tone === 'faint' ? color.textFaint : color.textMuted;
  return <Text style={[T.overline, { color: c }, style]}>{children}</Text>;
}

/* ───────────────── Screen + headers ───────────────── */

type ScreenProps = {
  children: React.ReactNode;
  header?: React.ReactNode;
  footer?: React.ReactNode;
  scroll?: boolean;
  edges?: Edge[];
  refreshControl?: ScrollViewProps['refreshControl'];
  contentStyle?: StyleProp<ViewStyle>;
  keyboardShouldPersistTaps?: ScrollViewProps['keyboardShouldPersistTaps'];
  testID?: string;
};

/** Page shell: safe area, night background, gutter, consistent bottom room. */
export function Screen({
  children, header, footer, scroll = true, edges = ['top'], refreshControl,
  contentStyle, keyboardShouldPersistTaps = 'handled', testID,
}: ScreenProps) {
  return (
    <SafeAreaView style={s.screen} edges={edges} testID={testID}>
      {header}
      {scroll ? (
        <ScrollView
          style={s.flex}
          contentContainerStyle={[s.scrollContent, contentStyle]}
          refreshControl={refreshControl}
          keyboardShouldPersistTaps={keyboardShouldPersistTaps}
          showsVerticalScrollIndicator={false}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={[s.flex, contentStyle]}>{children}</View>
      )}
      {footer}
    </SafeAreaView>
  );
}

/** Tab-screen header: overline (context) + large title, optional right slot. */
export function PageHeader({ overline, title, subtitle, right }: {
  overline?: string; title: string; subtitle?: string; right?: React.ReactNode;
}) {
  return (
    <View style={s.pageHeader}>
      <View style={s.flex}>
        {overline ? <Overline style={s.pageOverline}>{overline}</Overline> : null}
        <Text style={[T.display, { color: color.text }]} accessibilityRole="header">{title}</Text>
        {subtitle ? <Text style={[T.bodySm, s.pageSubtitle]}>{subtitle}</Text> : null}
      </View>
      {right ? <View style={s.pageHeaderRight}>{right}</View> : null}
    </View>
  );
}

/** Stack-screen nav bar: back, title, optional right slot. Hairline under. */
export function NavBar({ title, onBack, right, border = true, close }: {
  title?: string; onBack?: () => void; right?: React.ReactNode; border?: boolean; close?: boolean;
}) {
  return (
    <View style={[s.navBar, border && s.navBarBorder]}>
      <View style={s.navSide}>
        {onBack ? <IconButton icon={close ? X : ChevronLeft} label={close ? 'Close' : 'Back'} onPress={onBack} iconSize={22} /> : null}
      </View>
      <Text style={[T.callout, s.navTitle]} numberOfLines={1} accessibilityRole="header">{title}</Text>
      <View style={[s.navSide, s.navSideRight]}>{right}</View>
    </View>
  );
}

/** Pinned bottom action area for stack screens (respects home indicator). */
export function Footer({ children }: { children: React.ReactNode }) {
  return (
    <SafeAreaView edges={['bottom']} style={s.footer}>
      <View style={s.footerInner}>{children}</View>
    </SafeAreaView>
  );
}

/* ───────────────── Sections, groups, rows ───────────────── */

export function Section({ label, action, children, style, first }: {
  label?: string; action?: React.ReactNode; children: React.ReactNode;
  style?: StyleProp<ViewStyle>; first?: boolean;
}) {
  return (
    <View style={[!first && s.sectionGap, style]}>
      {label || action ? (
        <View style={s.sectionHead}>
          {label ? <Overline>{label}</Overline> : <View />}
          {action}
        </View>
      ) : null}
      {children}
    </View>
  );
}

/** Hairline-bounded list. Children are separated by hairlines automatically. */
export function Group({ children, style }: { children: React.ReactNode; style?: StyleProp<ViewStyle> }) {
  const items = React.Children.toArray(children).filter(Boolean);
  return (
    <View style={[s.group, style]}>
      {items.map((child, i) => (
        <View key={i} style={i > 0 ? s.groupDivider : undefined}>{child}</View>
      ))}
    </View>
  );
}

export function Card({ children, style, raised }: {
  children: React.ReactNode; style?: StyleProp<ViewStyle>; raised?: boolean;
}) {
  return <View style={[s.card, raised && s.cardRaised, style]}>{children}</View>;
}

type RowProps = {
  icon?: LucideIcon;
  title: string;
  subtitle?: string;
  value?: string;
  right?: React.ReactNode;
  onPress?: () => void;
  chevron?: boolean;
  tone?: 'default' | 'accent' | 'bad';
  disabled?: boolean;
  loading?: boolean;
  testID?: string;
  accessibilityLabel?: string;
};

export function Row({
  icon: Icon, title, subtitle, value, right, onPress, chevron, tone = 'default',
  disabled, loading, testID, accessibilityLabel,
}: RowProps) {
  const fg = tone === 'bad' ? color.bad : tone === 'accent' ? color.accentText : color.text;
  const iconColor = tone === 'default' ? color.textMuted : fg;
  const body = (
    <View style={[s.row, disabled && s.disabled]}>
      {Icon ? <Icon size={layout.icon} color={iconColor} strokeWidth={layout.iconStroke} /> : null}
      <View style={s.flex}>
        <Text style={[T.bodyMedium, { color: fg }]}>{title}</Text>
        {subtitle ? <Text style={[T.bodySm, s.rowSubtitle]}>{subtitle}</Text> : null}
      </View>
      {value ? <Text style={[T.data, s.rowValue]}>{value}</Text> : null}
      {right}
      {loading ? <ActivityIndicator size="small" color={color.textMuted} /> : null}
      {chevron && !loading ? <ChevronRight size={layout.icon} color={color.textFaint} strokeWidth={layout.iconStroke} /> : null}
    </View>
  );
  if (!onPress) return body;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || title}
      style={({ pressed }) => pressed && s.rowPressed}
    >
      {body}
    </Pressable>
  );
}

/** Two-column key/value line for detail blocks. Value wraps, never truncates. */
export function KeyValue({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <View style={s.kv}>
      <Text style={[T.bodySm, { color: color.textMuted }]}>{label}</Text>
      <Text style={[mono ? T.data : T.body, s.kvValue]}>{value}</Text>
    </View>
  );
}

/** Grid of mono stats split by hairlines (DS: grid-borders, never cards-in-cards). */
export function StatGrid({ items, large }: { items: { label: string; value: string; tone?: Tone }[]; large?: boolean }) {
  return (
    <View style={s.statGrid}>
      {items.map((it, i) => (
        <View key={it.label} style={[s.stat, i > 0 && s.statDivider]}>
          <Text style={[large ? T.kpiSm : T.dataMd, { color: it.tone ? toneColors[it.tone].fg : color.text }]}>{it.value}</Text>
          <Text style={[T.caption, { color: color.textMuted }]}>{it.label}</Text>
        </View>
      ))}
    </View>
  );
}

/* ───────────────── Controls ───────────────── */

type ButtonProps = {
  title: string;
  onPress?: () => void;
  variant?: 'primary' | 'accent' | 'secondary' | 'ghost' | 'danger';
  icon?: LucideIcon;
  iconRight?: LucideIcon;
  loading?: boolean;
  disabled?: boolean;
  full?: boolean;
  style?: StyleProp<ViewStyle>;
  testID?: string;
  accessibilityLabel?: string;
};

export function Button({
  title, onPress, variant = 'primary', icon: Icon, iconRight: IconRight, loading,
  disabled, full = true, style, testID, accessibilityLabel,
}: ButtonProps) {
  const v = buttonVariants[variant];
  const inactive = disabled || loading;
  return (
    <PressScale
      onPress={onPress}
      disabled={inactive}
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || title}
      accessibilityState={{ disabled: !!inactive, busy: !!loading }}
      style={[s.button, v.box, full && s.buttonFull, inactive && s.disabled, style]}
    >
      {loading ? (
        <ActivityIndicator size="small" color={v.fg} />
      ) : (
        <>
          {Icon ? <Icon size={layout.icon} color={v.fg} strokeWidth={layout.iconStroke} /> : null}
          <Text style={[T.callout, { color: v.fg }]}>{title}</Text>
          {IconRight ? <IconRight size={layout.icon} color={v.fg} strokeWidth={layout.iconStroke} /> : null}
        </>
      )}
    </PressScale>
  );
}

const buttonVariants = {
  primary: { box: { backgroundColor: color.accent }, fg: color.textOnAccent },
  accent: { box: { backgroundColor: color.accent }, fg: color.textOnAccent },
  secondary: { box: { borderWidth: hairline, borderColor: color.hairStrong }, fg: color.text },
  ghost: { box: {}, fg: color.textBody },
  danger: { box: { borderWidth: hairline, borderColor: color.bad }, fg: color.bad },
} as const;

export function IconButton({ icon: Icon, label, onPress, iconSize = layout.icon, tint, outline, badge, testID }: {
  icon: LucideIcon; label: string; onPress?: () => void; iconSize?: number;
  tint?: string; outline?: boolean; badge?: boolean; testID?: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      hitSlop={space[4]}
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => [s.iconButton, outline && s.iconButtonOutline, pressed && s.rowPressed]}
    >
      <Icon size={iconSize} color={tint || color.text} strokeWidth={layout.iconStroke} />
      {badge ? <View style={s.badge} /> : null}
    </Pressable>
  );
}

export function TextLink({ title, onPress, tone = 'muted', testID, style }: {
  title: string; onPress?: () => void; tone?: 'muted' | 'accent'; testID?: string; style?: StyleProp<ViewStyle>;
}) {
  return (
    <Pressable onPress={onPress} hitSlop={space[10]} testID={testID} accessibilityRole="link" style={[s.textLink, style]}>
      <Text style={[T.callout, { color: tone === 'accent' ? color.accentText : color.textMuted }]}>{title}</Text>
    </Pressable>
  );
}

type FieldProps = TextInputProps & { label?: string; hint?: string; error?: string; right?: React.ReactNode };

export const Field = React.forwardRef<TextInput, FieldProps>(function Field(
  { label, hint, error, right, style, onFocus, onBlur, ...rest }, ref,
) {
  const [focused, setFocused] = React.useState(false);
  return (
    <View style={s.field}>
      {label ? <Text style={[T.bodySm, s.fieldLabel]}>{label}</Text> : null}
      <View style={[s.fieldBox, focused && s.fieldBoxFocused, !!error && s.fieldBoxError]}>
        <TextInput
          ref={ref}
          placeholderTextColor={color.textFaint}
          selectionColor={color.accent}
          style={[T.body, s.fieldInput, style]}
          onFocus={(e) => { setFocused(true); onFocus?.(e); }}
          onBlur={(e) => { setFocused(false); onBlur?.(e); }}
          {...rest}
        />
        {right}
      </View>
      {error ? <Text style={[T.caption, { color: color.bad }]}>{error}</Text>
        : hint ? <Text style={[T.caption, { color: color.textFaint }]}>{hint}</Text> : null}
    </View>
  );
});

/** Segmented choice (2–4 options). */
export function Segmented<V extends string>({ options, value, onChange }: {
  options: { value: V; label: string }[]; value: V; onChange: (v: V) => void;
}) {
  return (
    <View style={s.segmented}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable
            key={o.value}
            onPress={() => onChange(o.value)}
            accessibilityRole="button"
            accessibilityState={{ selected: on }}
            style={[s.segment, on && s.segmentOn]}
          >
            <Text style={[T.callout, { color: on ? color.text : color.textMuted }]}>{o.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/** Selectable chip: 2px corners (never pill), inverse fill when active. 44pt tall. */
export function Chip({ label, selected, onPress }: { label: string; selected?: boolean; onPress?: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected: !!selected }}
      style={({ pressed }) => [s.chip, selected && s.chipOn, pressed && !selected && s.rowPressed]}
    >
      <Text style={[T.callout, { color: selected ? color.textInverse : color.textBody }]}>{label}</Text>
    </Pressable>
  );
}

/** On/off control. Square track and knob (2px corners): no pill/round switches in Bricks. */
export function Toggle({ value, onValueChange, disabled, accessibilityLabel }: {
  value: boolean; onValueChange: (v: boolean) => void; disabled?: boolean; accessibilityLabel?: string;
}) {
  return (
    <Pressable
      onPress={() => !disabled && onValueChange(!value)}
      disabled={disabled}
      hitSlop={space[10]}
      accessibilityRole="switch"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={{ checked: value, disabled: !!disabled }}
      style={[s.toggle, value && s.toggleOn, disabled && s.toggleDisabled]}
    >
      <View style={[s.toggleKnob, value ? s.toggleKnobOn : null]} />
    </Pressable>
  );
}

export function ChipRow({ children }: { children: React.ReactNode }) {
  return <View style={s.chipRow}>{children}</View>;
}

/* ───────────────── Status ───────────────── */

export function Pill({ label, tone = 'neutral', dot }: { label: string; tone?: Tone; dot?: boolean }) {
  const c = toneColors[tone];
  return (
    <View style={[s.pill, { backgroundColor: c.bg }]}>
      {dot ? <View style={[s.pillDot, { backgroundColor: c.fg }]} /> : null}
      <Text style={[T.pill, { color: c.fg }]}>{label}</Text>
    </View>
  );
}

export function Dot({ tone = 'accent' }: { tone?: Tone }) {
  return <View style={[s.pillDot, { backgroundColor: toneColors[tone].fg }]} />;
}

/** Tinted inline message; no border (DS: tinted bg + dot, never a painted box). */
export function Notice({ tone = 'neutral', title, children, icon: Icon }: {
  tone?: Tone; title?: string; children?: React.ReactNode; icon?: LucideIcon;
}) {
  const c = toneColors[tone];
  return (
    <View style={[s.notice, { backgroundColor: c.bg }]}>
      {Icon ? <Icon size={layout.icon} color={c.fg} strokeWidth={layout.iconStroke} /> : <View style={[s.pillDot, s.noticeDot, { backgroundColor: c.fg }]} />}
      <View style={s.flex}>
        {title ? <Text style={[T.bodyMedium, { color: color.text }]}>{title}</Text> : null}
        {typeof children === 'string'
          ? <Text style={[T.bodySm, { color: color.textBody }]}>{children}</Text>
          : children}
      </View>
    </View>
  );
}

export function EmptyState({ icon: Icon, title, body, action }: {
  icon?: LucideIcon; title: string; body?: string; action?: React.ReactNode;
}) {
  return (
    <View style={s.empty}>
      {Icon ? <Icon size={28} color={color.textFaint} strokeWidth={layout.iconStroke} /> : null}
      <Text style={[T.headline, s.centerText, { color: color.text }]}>{title}</Text>
      {body ? <Text style={[T.bodySm, s.centerText, { color: color.textMuted }]}>{body}</Text> : null}
      {action ? <View style={s.emptyAction}>{action}</View> : null}
    </View>
  );
}

export function LoadingState({ label }: { label?: string }) {
  return (
    <View style={s.loading}>
      <ActivityIndicator color={color.accent} />
      {label ? <Text style={[T.bodySm, { color: color.textMuted }]}>{label}</Text> : null}
    </View>
  );
}

export function Avatar({ name, size = 44 }: { name?: string | null; size?: number }) {
  const initials = (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join('');
  return (
    <View style={[s.avatar, { width: size, height: size }]}>
      <Text style={[T.callout, { color: color.accentText }]}>{initials}</Text>
    </View>
  );
}

export function Check({ checked }: { checked: boolean }) {
  return <View style={[s.check, checked && s.checkOn]} />;
}

export function Divider({ style }: { style?: StyleProp<ViewStyle> }) {
  return <View style={[s.divider, style]} />;
}

/* ───────────────── Styles ───────────────── */

const s = StyleSheet.create({
  flex: { flex: 1 },
  screen: { flex: 1, backgroundColor: color.bg },
  scrollContent: { paddingHorizontal: layout.gutter, paddingBottom: space[48] },
  disabled: { opacity: 0.45 },
  centerText: { textAlign: 'center' },

  pageHeader: {
    flexDirection: 'row', alignItems: 'flex-end', gap: space[12],
    paddingTop: space[16], paddingBottom: space[24],
  },
  pageOverline: { marginBottom: space[8] },
  pageSubtitle: { color: color.textMuted, marginTop: space[6] },
  pageHeaderRight: { flexDirection: 'row', alignItems: 'center', gap: space[4] },

  navBar: {
    height: 52, flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: space[8], backgroundColor: color.bg,
  },
  navBarBorder: { borderBottomWidth: hairline, borderBottomColor: color.hair },
  navSide: { minWidth: 88, flexDirection: 'row', alignItems: 'center' },
  navSideRight: { justifyContent: 'flex-end', paddingRight: space[4] },
  navTitle: { flex: 1, textAlign: 'center', color: color.text },

  footer: { backgroundColor: color.bg, borderTopWidth: hairline, borderTopColor: color.hair },
  footerInner: { paddingHorizontal: layout.gutter, paddingTop: space[12], paddingBottom: space[12], gap: space[10] },

  sectionGap: { marginTop: layout.section },
  sectionHead: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    minHeight: 20, marginBottom: space[10],
  },

  group: { borderWidth: hairline, borderColor: color.hair, borderRadius: radius.sm },
  groupDivider: { borderTopWidth: hairline, borderTopColor: color.hair },
  card: { borderWidth: hairline, borderColor: color.hair, borderRadius: radius.sm, padding: space[16] },
  cardRaised: { backgroundColor: color.surface },

  row: {
    minHeight: layout.rowMin, flexDirection: 'row', alignItems: 'center', gap: space[12],
    paddingHorizontal: space[16], paddingVertical: space[12],
  },
  rowPressed: { backgroundColor: color.surface },
  rowSubtitle: { color: color.textMuted, marginTop: 2 },
  rowValue: { color: color.textMuted, flexShrink: 0 },

  kv: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: space[16], paddingVertical: space[10] },
  kvValue: { color: color.text, flexShrink: 1, textAlign: 'right' },

  statGrid: { flexDirection: 'row', borderWidth: hairline, borderColor: color.hair, borderRadius: radius.sm },
  stat: { flex: 1, paddingVertical: space[14], paddingHorizontal: space[14], gap: space[4] },
  statDivider: { borderLeftWidth: hairline, borderLeftColor: color.hair },

  button: {
    minHeight: layout.control, borderRadius: radius.sm, paddingHorizontal: space[20],
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space[8],
  },
  buttonFull: { alignSelf: 'stretch' },

  iconButton: { width: layout.tap, height: layout.tap, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm },
  iconButtonOutline: { borderWidth: hairline, borderColor: color.hair },
  badge: {
    position: 'absolute', top: space[10], right: space[10], width: space[8], height: space[8],
    borderRadius: radius.pill, backgroundColor: color.accent, borderWidth: 2, borderColor: color.bg,
  },
  textLink: { minHeight: layout.tap, justifyContent: 'center', alignSelf: 'center', paddingHorizontal: space[8] },

  field: { gap: space[6] },
  fieldLabel: { color: color.textMuted },
  fieldBox: {
    minHeight: layout.control, flexDirection: 'row', alignItems: 'center',
    borderWidth: hairline, borderColor: color.hairStrong, borderRadius: radius.sm,
    backgroundColor: color.surface, paddingHorizontal: space[14],
  },
  fieldBoxFocused: { borderColor: color.accent },
  fieldBoxError: { borderColor: color.bad },
  fieldInput: { flex: 1, color: color.text, paddingVertical: space[12] },

  segmented: { flexDirection: 'row', borderWidth: hairline, borderColor: color.hair, borderRadius: radius.sm, padding: 2 },
  segment: { flex: 1, minHeight: layout.tap - 4, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm },
  segmentOn: { backgroundColor: color.surfaceDeep },

  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: space[8] },
  chip: {
    minHeight: layout.tap, paddingHorizontal: space[16], borderRadius: radius.sm,
    borderWidth: hairline, borderColor: color.hair, backgroundColor: color.surface,
    alignItems: 'center', justifyContent: 'center',
  },
  toggle: {
    width: 44, height: 26, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hairStrong,
    backgroundColor: color.surfaceDeep, padding: 3, justifyContent: 'center',
  },
  toggleOn: { backgroundColor: color.accent, borderColor: color.accent },
  toggleDisabled: { opacity: 0.5 },
  toggleKnob: { width: 18, height: 18, borderRadius: radius.sm, backgroundColor: color.textMuted },
  toggleKnobOn: { alignSelf: 'flex-end', backgroundColor: color.textOnAccent },
  chipOn: { borderColor: color.inverse, backgroundColor: color.inverse },

  pill: {
    flexDirection: 'row', alignItems: 'center', gap: space[6], alignSelf: 'flex-start',
    paddingHorizontal: space[8], paddingVertical: space[4], borderRadius: radius.sm,
  },
  pillDot: { width: 6, height: 6, borderRadius: radius.pill },

  notice: { flexDirection: 'row', alignItems: 'flex-start', gap: space[12], padding: space[14], borderRadius: radius.sm },
  noticeDot: { marginTop: space[8] },

  empty: { alignItems: 'center', gap: space[8], paddingVertical: space[48], paddingHorizontal: space[24] },
  emptyAction: { marginTop: space[16], alignSelf: 'stretch' },
  loading: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space[12], paddingVertical: space[48] },

  avatar: { borderRadius: radius.sm, backgroundColor: color.accentTint, alignItems: 'center', justifyContent: 'center' },
  check: { width: 18, height: 18, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hairStrong },
  checkOn: { backgroundColor: color.accent, borderColor: color.accent },
  divider: { height: hairline, backgroundColor: color.hair },
});
