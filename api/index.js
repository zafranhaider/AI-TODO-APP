export default async function handler(req, res) {
  try {
    res.status(200).json({
      success: true,
      message: "Serverless function is running successfully on Vercel 🚀"
    });
  } catch (error) {
    console.error("Error in serverless function:", error);
    res.status(500).json({ success: false, error: error.message });
  }
}
