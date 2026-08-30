import { Tabs } from 'expo-router';
import { Home, CalendarDays, Upload, DollarSign, User } from 'lucide-react-native';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { BlurView } from 'expo-blur';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const TABS = [
  { name: 'index',          label: 'Today',    Icon: Home },
  { name: 'schedule',       label: 'Week',     Icon: CalendarDays },
  { name: 'uploads',        label: 'Upload',   Icon: Upload },
  { name: 'payouts/index',  label: 'Pay',      Icon: DollarSign },
  { name: 'account',        label: 'Me',       Icon: User },
];

function BricksTabBar({ state, descriptors, navigation }: any) {
  const insets = useSafeAreaInsets();

  const visibleRoutes = state.routes.filter((r: any) =>
    TABS.some(t => t.name === r.name)
  );

  return (
    <View style={[s.wrapper, { bottom: insets.bottom + 16 }]}>
      <BlurView intensity={60} tint="dark" style={s.blur}>
        <View style={s.inner}>
          {visibleRoutes.map((route: any) => {
            const tab = TABS.find(t => t.name === route.name);
            if (!tab) return null;
            const focused = state.routes[state.index]?.name === route.name;
            const { Icon } = tab;

            return (
              <TouchableOpacity
                key={route.key}
                style={[s.tab, focused && s.tabActive]}
                onPress={() => navigation.navigate(route.name)}
                activeOpacity={0.8}
              >
                <Icon
                  size={18}
                  color={focused ? TOKENS.moon : TOKENS.moonFaint}
                  strokeWidth={focused ? 1.8 : 1.5}
                />
                <Text style={[s.label, focused && s.labelActive]}>
                  {tab.label.toUpperCase()}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </BlurView>
    </View>
  );
}

export default function TabsLayout() {
  return (
    <Tabs
      tabBar={(props) => <BricksTabBar {...props} />}
      screenOptions={{ headerShown: false }}
    >
      <Tabs.Screen name="index" />
      <Tabs.Screen name="schedule" />
      <Tabs.Screen name="uploads" />
      <Tabs.Screen name="payouts/index" />
      <Tabs.Screen name="account" />
    </Tabs>
  );
}

const s = StyleSheet.create({
  wrapper: {
    position: 'absolute',
    left: 20,
    right: 20,
    alignItems: 'stretch',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.4,
    shadowRadius: 24,
    elevation: 16,
  },
  blur: {
    borderRadius: 2,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: TOKENS.nightHairS,
  },
  inner: {
    flexDirection: 'row',
    backgroundColor: 'rgba(20, 18, 16, 0.72)',
  },
  tab: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 12,
    gap: 5,
  },
  tabActive: {
    backgroundColor: TOKENS.nightDeep,
  },
  label: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 8,
    color: TOKENS.moonFaint,
    letterSpacing: 1.2,
  },
  labelActive: {
    color: TOKENS.moon,
  },
});
