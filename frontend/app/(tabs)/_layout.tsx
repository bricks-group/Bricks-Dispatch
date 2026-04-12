import { Tabs } from 'expo-router';
import { View, Text, StyleSheet } from 'react-native';
import { Briefcase, Calendar, Upload, User } from 'lucide-react-native';

type TabIconProps = {
  Icon: React.ComponentType<{ size: number; color: string; strokeWidth: number }>;
  label: string;
  focused: boolean;
};

function TabIcon({ Icon, label, focused }: TabIconProps) {
  return (
    <View style={styles.tabItem}>
      <Icon
        size={22}
        color={focused ? '#FACC15' : '#71717A'}
        strokeWidth={focused ? 2.5 : 2}
      />
      <Text style={[styles.tabLabel, focused && styles.tabLabelActive]}>
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
        tabBarActiveTintColor: '#FACC15',
        tabBarInactiveTintColor: '#71717A',
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: 'Jobs',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={Briefcase} label="JOBS" focused={focused} />
          ),
          tabBarTestID: 'tab-jobs',
        }}
      />
      <Tabs.Screen
        name="schedule"
        options={{
          title: 'Schedule',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={Calendar} label="SCHEDULE" focused={focused} />
          ),
          tabBarTestID: 'tab-schedule',
        }}
      />
      <Tabs.Screen
        name="uploads"
        options={{
          title: 'Uploads',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={Upload} label="UPLOADS" focused={focused} />
          ),
          tabBarTestID: 'tab-uploads',
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarIcon: ({ focused }) => (
            <TabIcon Icon={User} label="PROFILE" focused={focused} />
          ),
          tabBarTestID: 'tab-profile',
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    backgroundColor: '#0A0A0A',
    borderTopWidth: 1,
    borderTopColor: '#1A1A1A',
    height: 72,
    paddingTop: 8,
    paddingBottom: 12,
    elevation: 0,
    shadowOpacity: 0,
  },
  tabItem: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  },
  tabLabel: {
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.5,
    color: '#71717A',
  },
  tabLabelActive: {
    color: '#FACC15',
  },
});
