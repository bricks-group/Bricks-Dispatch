// @ts-nocheck
import { ScrollViewStyleReset } from "expo-router/html";
import type { PropsWithChildren } from "react";

export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en" style={{ height: "100%" }}>
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta
          name="viewport"
          content="width=device-width, initial-scale=1, viewport-fit=cover"
        />
        <meta name="theme-color" content="#141210" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        {/*
          Disable body scrolling on web to make ScrollView components work correctly.
          If you want to enable scrolling, remove `ScrollViewStyleReset` and
          set `overflow: auto` on the body style below.
        */}
        <ScrollViewStyleReset />
        <style
          dangerouslySetInnerHTML={{
            __html: `
              html {
                height: 100%;
                height: 100dvh;
                -webkit-tap-highlight-color: transparent;
                touch-action: manipulation;
              }
              body {
                margin: 0;
                height: 100%;
                height: 100dvh;
                overflow: hidden;
                overscroll-behavior: none;
                background-color: #141210;
              }
              /* Root fills the visual viewport; SafeAreaView owns insets (no double pad). */
              body > div:first-child {
                position: fixed !important;
                top: 0; left: 0; right: 0; bottom: 0;
              }
              #root, [data-rn-root] {
                min-height: 100%;
                min-height: 100dvh;
              }
              [role="tablist"] [role="tab"] * { overflow: visible !important; }
              [role="heading"], [role="heading"] * { overflow: visible !important; }
              button, [role="button"], a {
                -webkit-user-select: none;
                user-select: none;
                -webkit-touch-callout: none;
              }
              /* Avoid iOS focus zoom without disabling pinch-zoom */
              input, textarea, select { font-size: 16px; }
            `,
          }}
        />
      </head>
      <body
        style={{
          margin: 0,
          height: "100%",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          backgroundColor: "#141210",
        }}
      >
        {children}
      </body>
    </html>
  );
}
