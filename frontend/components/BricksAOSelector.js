import React, { useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { WebView } from 'react-native-webview';

// Bricks Design Tokens
const BRICK = '#D2542B';
const NIGHT = '#141210';
const MOON = '#F4F1EB';

const BRICKS_MAP_STYLE = [
  { "elementType": "geometry", "stylers": [{ "color": "#141210" }] },
  { "featureType": "water", "stylers": [{ "color": "#1C1A17" }] },
  { "featureType": "road", "elementType": "geometry", "stylers": [{ "color": "#23201C" }] },
  { "featureType": "landscape", "stylers": [{ "color": "#141210" }] },
  { "featureType": "poi", "stylers": [{ "visibility": "off" }] },
  { "featureType": "transit", "stylers": [{ "visibility": "off" }] },
  { "featureType": "road", "elementType": "labels.text.fill", "stylers": [{ "color": "#5E574C" }] },
  { "featureType": "administrative", "elementType": "labels.text.fill", "stylers": [{ "color": "#8F8678" }] }
];

export default function BricksAOSelector({ onPolygonComplete }) {
  const [points, setPoints] = useState([]);

  const mapHtml = `
    <!DOCTYPE html>
    <html>
      <head>
        <meta name="viewport" content="initial-scale=1.0, user-scalable=no">
        <meta charset="utf-8">
        <style>
          #map { height: 100%; width: 100%; position: absolute; top: 0; left: 0; background: #141210; }
          body { margin: 0; padding: 0; }
        </style>
      </head>
      <body>
        <div id="map"></div>
        <script>
          let map;
          let polygon;
          let markers = [];
          let path = [];

          function initMap() {
            map = new google.maps.Map(document.getElementById('map'), {
              center: {lat: 43.6532, lng: -79.3832}, // Toronto
              zoom: 10,
              styles: ${JSON.stringify(BRICKS_MAP_STYLE)},
              disableDefaultUI: true,
              backgroundColor: '#141210'
            });

            map.addListener('click', (e) => {
              const lat = e.latLng.lat();
              const lng = e.latLng.lng();
              addPoint(lat, lng);
            });
          }

          function addPoint(lat, lng) {
            path.push({lat, lng});
            
            // Draw Marker (The "Dot")
            const marker = new google.maps.Circle({
              strokeColor: '${BRICK}',
              strokeOpacity: 1,
              strokeWeight: 0,
              fillColor: '${BRICK}',
              fillOpacity: 1.0,
              map,
              center: {lat, lng},
              radius: 400 // Fixed visual size
            });
            markers.push(marker);

            if (polygon) polygon.setMap(null);

            // Draw Polygon (The "Area of Operations")
            polygon = new google.maps.Polygon({
              paths: path,
              strokeColor: '${BRICK}',
              strokeOpacity: 0.8,
              strokeWeight: 1, // Hairline
              fillColor: '${BRICK}',
              fillOpacity: 0.1,
              map
            });

            // Send data back to React Native
            window.ReactNativeWebView.postMessage(JSON.stringify(path));
          }
        </script>
        <script src="https://maps.googleapis.com/maps/api/js?key=${process.env.EXPO_PUBLIC_GOOGLE_MAPS_KEY}&callback=initMap" async defer></script>
      </body>
    </html>
  `;

  return (
    <View style={styles.container}>
      <Text style={styles.label}>AREA OF OPERATIONS (AO)</Text>
      <Text style={styles.help}>Tap the map to connect dots and define your territory.</Text>
      <View style={styles.mapWrapper}>
        <WebView 
          source={{ html: mapHtml }}
          style={styles.map}
          onMessage={(event) => {
            const path = JSON.parse(event.nativeEvent.data);
            onPolygonComplete(path);
          }}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: 32,
  },
  label: {
    fontSize: 10,
    fontFamily: 'IoskeleyMono-Medium',
    color: '#8F8678',
    letterSpacing: 1.6,
    marginBottom: 8,
  },
  help: {
    fontSize: 11,
    color: '#5E574C',
    marginBottom: 16,
  },
  mapWrapper: {
    height: 300,
    borderWidth: 1,
    borderColor: '#F4F1EB14',
    borderRadius: 2,
    overflow: 'hidden',
  },
  map: {
    flex: 1,
  }
});
