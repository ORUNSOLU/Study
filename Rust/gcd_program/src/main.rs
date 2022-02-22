use std::{env, str::FromStr};

fn main() {
   
   let mut numbers = Vec::new();

   // first argument is program name, so skip
   for arg in env::args().skip(1) {
      numbers.push(u64::from_str(&arg).expect("error parsing argument"));
   }

   // exit if Vec is empty
   if numbers.len() == 0 {
      eprintln!("Usage: gcd NUMBER ...");
      std::process::exit(1);
   }

   let mut d = numbers[0];
   for m in &numbers[1..] {
      d = gcd(*m, d);
   }
   println!("The greatest common divisor of {:?} is {}", numbers, d);

}


fn gcd(mut m: u64, mut n: u64) -> u64 {
   assert!(m != 0 && n != 0);
   while m != 0 {
      if m < n {
         let t = m;
         m = n;
         n = t;
      }
      m = m % n;
   }
   n
}

#[test]
fn test_gcd() {
   assert_eq!(gcd(15, 14), 1);
   assert_eq!(gcd(3 * 7 * 11 * 13 * 19, 2 * 3 * 5 * 11 * 17), 3 * 11);
}