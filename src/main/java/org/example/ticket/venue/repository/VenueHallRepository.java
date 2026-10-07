package org.example.ticket.venue.repository;

import org.example.ticket.venue.domain.VenueHall;
import org.springframework.data.jpa.repository.JpaRepository;

public interface VenueHallRepository extends JpaRepository<VenueHall, Long> {
}
