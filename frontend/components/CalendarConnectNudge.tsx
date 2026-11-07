import { Calendar } from 'lucide-react-native';
import { Text, View, StyleSheet } from 'react-native';
import { CALENDAR_NUDGE_BODY, CALENDAR_NUDGE_TITLE } from '../lib/calendarConnect';
import { color, space, type as T } from '../theme';
import { Notice, TextLink } from './ui';

/** Visible empty-state on Today / Settings when no calendar is connected. Not a toast. */
export function CalendarConnectNudge({
  actionLabel = 'Open Settings',
  onPress,
}: {
  actionLabel?: string;
  onPress?: () => void;
}) {
  return (
    <View style={s.wrap} testID="calendar-connect-nudge">
      <Notice tone="warn" title={CALENDAR_NUDGE_TITLE} icon={Calendar}>
        <Text style={[T.bodySm, { color: color.textBody }]}>{CALENDAR_NUDGE_BODY}</Text>
        {onPress ? (
          <TextLink title={actionLabel} tone="accent" onPress={onPress} style={s.link} />
        ) : null}
      </Notice>
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { marginBottom: space[16] },
  link: { marginTop: space[8], alignSelf: 'flex-start' },
});
