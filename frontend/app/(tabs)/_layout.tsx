import { Tabs } from 'expo-router';
import { View, Text, StyleSheet, Platform } from 'react-native';
import { Briefcase, Calendar, Upload, User } from 'lucide-react-native';
import { TOKENS } from '../../constants/Theme';

type TabIconProps = {
  Icon: React.ComponentType<{ size: number; color: string; strokeWidth: number }>;
  label: string;
  focused: boolean;
};

function TabIcon({ Icon, label, focused }: TabIconProps) {
  return (
    <View style={styles.tabItem}>
      {focused && <View style={styles.activeIndicator} />}
      <Icon
        size={20}
        color={focused ? TOKENS.moon : TOKENS.moonFaint}
        strokeWidth={focused ? 1.75 : 1.5}
      />
      <Text 
        numberOfLines={1} 
        style={[styles.tabLabel, focused && styles.tabLabelActive]}
      >
        {label}
      </Text>
    </View>
  );
}

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: styles.tabBar,
        tabBarShowLabel: false,
        tabBarActiveTintColor: TOKENS.moon,
        tabBarInactiveTintColor: TOKENS.moonFaint,
        sceneStyle: { backgroundColor: TOKENS.night },
        ...(Platform.OS === 'web' ? { unmountOnBlur: true } : {}),
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'JOBS',
          tabBarIcon: ({ focused }) => <TabIcon Icon={Briefcase} label="JOBS" focused={focused} />,
          tabBarTestID: 'tab-jobs',
        }}
      />
      <Tabs.Screen
        name="schedule"
        options={{
          title: 'SCHEDULE',
          tabBarIcon: ({ focused }) => <TabIcon Icon={Calendar} label="SCHEDULE" focused={focused} />,
          tabBarTestID: 'tab-schedule',
        }}
      />
      <Tabs.Screen
        name="uploads"
        options={{
          title: 'UPLOADS',
          tabBarIcon: ({ focused }) => <TabIcon Icon={Upload} label="UPLOADS" focused={focused} />,
          tabBarTestID: 'tab-uploads',
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'PROFILE',
          tabBarIcon: ({ focused }) => <TabIcon Icon={User} label="PROFILE" focused={focused} />,
          tabBarTestID: 'tab-profile',
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    backgroundColor: TOKENS.night,
    borderTopWidth: 1,
    borderTopColor: TOKENS.nightHair,
    height: 84,
    paddingTop: 12,
    paddingBottom: 28,
    elevation: 0,
    shadowOpacity: 0,
  },
  tabItem: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    position: 'relative',
    width: '100%',
  },
  activeIndicator: {
    position: 'absolute',
    top: -12,
    width: 24,
    height: 2,
    backgroundColor: TOKENS.brick,
  },
  tabLabel: {
    fontSize: 9, // Slightly smaller to prevent wrap
    fontFamily: 'JetBrainsMono-Medium',
    color: TOKENS.moonFaint,
    letterSpacing: 1.0, // Reduced from 1.2
    textTransform: 'uppercase',
  },
  tabLabelActive: {
    color: TOKENS.moon,
  },
});
