import { Tabs } from 'expo-router';
import { Home, CalendarDays, Upload, DollarSign, User } from 'lucide-react-native';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { color, type as T, space, layout, hairline } from '../../theme';

const TABS = [
  { name: 'index',          label: 'Today',    Icon: Home },
  { name: 'schedule',       label: 'Week',     Icon: CalendarDays },
  { name: 'uploads',        label: 'Upload',   Icon: Upload },
  { name: 'payouts/index',  label: 'Pay',      Icon: DollarSign },
  { name: 'account',        label: 'Me',       Icon: User },
];

/**
 * Flat, edge-to-edge tab bar (DS: no blur, no shadow on static UI).
 * Hairline top border, brick indicator on the active tab, home-indicator inset.
 */
function BricksTabBar({ state, navigation }: any) {
  const insets = useSafeAreaInsets();
  const visibleRoutes = state.routes.filter((r: any) => TABS.some((t) => t.name === r.name));

  return (
    <View style={[s.bar, { paddingBottom: Math.max(insets.bottom, space[8]) }]}>
      {visibleRoutes.map((route: any) => {
        const tab = TABS.find((t) => t.name === route.name);
        if (!tab) return null;
        const focused = state.routes[state.index]?.name === route.name;
        const { Icon } = tab;
        const tint = focused ? color.text : color.textFaint;
        return (
          <Pressable
            key={route.key}
            style={s.tab}
            onPress={() => {
              const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
              if (!focused && !event.defaultPrevented) navigation.navigate(route.name);
            }}
            accessibilityRole="tab"
            accessibilityState={{ selected: focused }}
            accessibilityLabel={tab.label}
          >
            <View style={[s.indicator, focused && s.indicatorOn]} />
            <Icon size={20} color={tint} strokeWidth={focused ? 1.75 : layout.iconStroke} />
            <Text style={[T.tabLabel, { color: tint }]}>{tab.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export default function TabsLayout() {
  return (
    <Tabs tabBar={(props) => <BricksTabBar {...props} />} screenOptions={{ headerShown: false }}>
      <Tabs.Screen name="index" />
      <Tabs.Screen name="schedule" />
      <Tabs.Screen name="uploads" />
      <Tabs.Screen name="payouts/index" />
      <Tabs.Screen name="account" />
    </Tabs>
  );
}

const s = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    backgroundColor: color.bg,
    borderTopWidth: hairline,
    borderTopColor: color.hair,
  },
  tab: {
    flex: 1,
    minHeight: 56,
    alignItems: 'center',
    justifyContent: 'center',
    gap: space[6],
    paddingTop: space[8],
  },
  indicator: {
    position: 'absolute',
    top: -hairline,
    width: space[24],
    height: 2,
    backgroundColor: 'transparent',
  },
  indicatorOn: { backgroundColor: color.accent },
});
